"""
Aequitas AI — Corpus ingestion (v2: COLD Cases, real metadata).

Replaces the v1 common-pile ingestion. Key differences:
- Source: harvard-lil/cold-cases (CourtListener-backed, real structured
  fields) instead of common-pile/caselaw_access_project (raw text only).
- No header-regex parsing, no synthetic citation generation — citation,
  court, jurisdiction, date, disposition all come directly from the
  dataset's own fields.
- Filtered to federal courts only (court_jurisdiction == "USA, Federal"),
  to preserve domain continuity with the existing Fourth Amendment /
  search-and-seizure corpus and prior adversarial test cases.
- is_overruled is seeded with a deterministic synthetic ~10% flag (hash
  of case_name), since no open dataset carries real Shepard's/KeyCite
  overruled-status data. See docs/DECISIONS.md, "Data gap fix" entry.
- Resumable: checks which citations already exist in Supabase before
  processing, so a Colab disconnect/reconnect mid-run does not duplicate
  rows or require restarting from scratch.
- Batched: embeds and inserts in batches rather than one row at a time,
  to reduce network round-trips (matters more when running from Colab
  against a remote Supabase instance).
- Device-aware: uses CUDA automatically if available (e.g. a Colab GPU
  runtime), falls back to CPU otherwise — no code change needed either way.

Run locally:
    export SUPABASE_URL=...
    export SUPABASE_SERVICE_ROLE_KEY=...
    python3 ingest_corpus.py

Run in Colab:
    - Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY as Colab Secrets
      (the key icon in the left sidebar), toggle "Notebook access" on
      for each.
    - !pip install -q supabase sentence-transformers langchain-text-splitters datasets
    - Paste this file's contents into a cell, or !wget it, then run.
"""

import hashlib
import os
import sys
import time

from datasets import load_dataset
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from supabase import create_client

# ============================================================
# Config
# ============================================================

MAX_CASES = int(os.environ.get("MAX_CASES", "5000"))  # tune after a throughput test batch
BATCH_SIZE = 50  # cases per embed+insert batch
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200
DATASET_PATH = "harvard-lil/cold-cases"
FEDERAL_JURISDICTION = "USA, Federal"

OVERRULED_FRACTION_PERCENT = 10  # deterministic synthetic ~10% flag


def get_credentials():
    """Reads Supabase credentials from Colab Secrets if running in Colab,
    otherwise from environment variables (local .env / shell export)."""
    try:
        from google.colab import userdata  # noqa: F401

        url = userdata.get("SUPABASE_URL")
        key = userdata.get("SUPABASE_SERVICE_ROLE_KEY")
        print("Running in Colab — using Colab Secrets for credentials.")
        return url, key
    except ImportError:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            print(
                "Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY. "
                "Set them as environment variables (local) or Colab Secrets.",
                file=sys.stderr,
            )
            sys.exit(1)
        return url, key


def is_overruled_synthetic(case_name: str) -> bool:
    """Deterministic ~10% synthetic overruled-status flag, based on a hash
    of the case name — same case always gets the same flag on re-runs.
    Real Shepard's/KeyCite overruled-status data is not available in any
    open dataset; this is a dev-corpus stand-in only. See DECISIONS.md."""
    hash_val = int(hashlib.md5(case_name.encode("utf-8")).hexdigest(), 16)
    return (hash_val % 100) < OVERRULED_FRACTION_PERCENT


def extract_raw_text(case: dict) -> str:
    """Concatenates all opinions' text in order (majority first, then any
    dissents/concurrences), since COLD Cases stores opinions as a list."""
    opinions = case.get("opinions") or []
    parts = []
    for op in opinions:
        text = (op.get("opinion_text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def parse_case(case: dict) -> dict | None:
    """Maps one COLD Cases record to our cases table shape. Returns None
    for records missing a usable citation or opinion text (cannot satisfy
    the NOT NULL UNIQUE citation constraint, or would embed nothing)."""
    citations = case.get("citations") or []
    if not citations:
        return None

    raw_text = extract_raw_text(case)
    if not raw_text:
        return None

    case_name = case.get("case_name") or case.get("case_name_full") or ""
    if not case_name:
        return None

    return {
        "citation": citations[0],
        "case_name": case_name,
        "court": case.get("court_full_name"),
        "jurisdiction": case.get("court_jurisdiction"),
        "decision_date": case.get("date_filed"),  # already ISO-ish (YYYY-MM-DD) in COLD Cases
        "is_overruled": is_overruled_synthetic(case_name),
        "raw_text": raw_text,
        "source": "COLD_CASES",
        # not inserted directly, used for chunking/embedding below:
        "_disposition": case.get("disposition"),
        "_precedential_status": case.get("precedential_status"),
    }


def chunk_text(text: str, splitter: RecursiveCharacterTextSplitter) -> list[str]:
    return splitter.split_text(text)


def fetch_existing_citations(client) -> set[str]:
    """Loads every citation already in the cases table, so this run skips
    rows a prior (possibly disconnected) run already inserted."""
    print("Checking for already-ingested cases (resumability)...")
    existing = set()
    page_size = 1000
    offset = 0
    while True:
        resp = (
            client.table("cases")
            .select("citation")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        existing.update(row["citation"] for row in rows)
        offset += page_size
        if len(rows) < page_size:
            break
    print(f"Found {len(existing)} cases already ingested — will skip these.")
    return existing


def insert_batch(client, model, splitter, parsed_cases: list[dict]) -> int:
    """Inserts a batch of parsed cases and their chunks. Embeds all chunks
    across the whole batch in one model.encode() call for efficiency.
    Returns the number of cases actually inserted."""
    if not parsed_cases:
        return 0

    # Insert case rows first, batched, to get their generated ids back.
    case_rows = [
        {
            "citation": c["citation"],
            "case_name": c["case_name"],
            "court": c["court"],
            "jurisdiction": c["jurisdiction"],
            "decision_date": c["decision_date"],
            "is_overruled": c["is_overruled"],
            "raw_text": c["raw_text"],
            "source": c["source"],
        }
        for c in parsed_cases
    ]

    try:
        resp = client.table("cases").insert(case_rows).execute()
    except Exception as e:
        # A batch can fail wholesale on a single bad row (e.g. a citation
        # collision within the dataset itself). Fall back to one-by-one
        # for this batch only, so one bad case doesn't lose the rest.
        print(f"  Batch insert failed ({e}); retrying cases individually...")
        inserted_rows = []
        for row in case_rows:
            try:
                r = client.table("cases").insert(row).execute()
                inserted_rows.extend(r.data)
            except Exception as inner_e:
                print(f"    Skipping case {row['citation']!r}: {inner_e}")
        resp_data = inserted_rows
    else:
        resp_data = resp.data

    if not resp_data:
        return 0

    citation_to_id = {row["citation"]: row["id"] for row in resp_data}

    # Build all chunks for all successfully-inserted cases in this batch.
    all_chunk_texts = []
    chunk_meta = []  # (case_id, chunk_index)
    for c in parsed_cases:
        case_id = citation_to_id.get(c["citation"])
        if case_id is None:
            continue  # this case failed to insert above, skip its chunks
        chunks = chunk_text(c["raw_text"], splitter)
        for idx, chunk in enumerate(chunks):
            all_chunk_texts.append(chunk)
            chunk_meta.append((case_id, idx, chunk))

    if not all_chunk_texts:
        return len(citation_to_id)

    # Embed all chunks in this batch in one call (no query-prefix here —
    # that prefix is only for query-side embeddings at retrieval time).
    embeddings = model.encode(
        [m[2] for m in chunk_meta], show_progress_bar=False, convert_to_numpy=True
    )

    chunk_rows = [
        {
            "case_id": case_id,
            "chunk_index": idx,
            "chunk_text": chunk,
            "embedding": embedding.tolist(),
        }
        for (case_id, idx, chunk), embedding in zip(chunk_meta, embeddings)
    ]

    # Insert chunks in sub-batches to keep individual requests reasonably sized.
    CHUNK_INSERT_BATCH = 200
    for i in range(0, len(chunk_rows), CHUNK_INSERT_BATCH):
        client.table("case_chunks").insert(chunk_rows[i : i + CHUNK_INSERT_BATCH]).execute()

    return len(citation_to_id)


def main():
    url, key = get_credentials()
    client = create_client(url, key)

    print(f"Loading embedding model {EMBEDDING_MODEL} (device auto-detected)...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    device = model.device
    print(f"Using device: {device}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ".", " ", ""],
    )

    existing_citations = fetch_existing_citations(client)

    print(f"Streaming {DATASET_PATH}, filtering to {FEDERAL_JURISDICTION}...")
    ds = load_dataset(DATASET_PATH, split="train", streaming=True)

    batch: list[dict] = []
    total_inserted = 0
    total_scanned = 0
    start_time = time.time()

    for raw_case in ds:
        if total_inserted >= MAX_CASES:
            break

        total_scanned += 1
        if raw_case.get("court_jurisdiction") != FEDERAL_JURISDICTION:
            continue

        parsed = parse_case(raw_case)
        if parsed is None:
            continue
        if parsed["citation"] in existing_citations:
            continue

        batch.append(parsed)
        existing_citations.add(parsed["citation"])  # avoid in-run duplicates too

        if len(batch) >= BATCH_SIZE:
            inserted = insert_batch(client, model, splitter, batch)
            total_inserted += inserted
            elapsed = time.time() - start_time
            rate = total_inserted / elapsed if elapsed > 0 else 0
            print(
                f"Inserted {total_inserted}/{MAX_CASES} cases "
                f"(scanned {total_scanned}, {rate:.2f} cases/sec)"
            )
            batch = []

    if batch:
        inserted = insert_batch(client, model, splitter, batch)
        total_inserted += inserted

    elapsed = time.time() - start_time
    print(
        f"\nDone. Inserted {total_inserted} new federal cases "
        f"(scanned {total_scanned} total records) in {elapsed:.1f}s."
    )
    print(
        "Next: run scripts/build_citations.py, then spot-check is_overruled "
        "and a few real citations in Supabase."
    )


if __name__ == "__main__":
    main()
