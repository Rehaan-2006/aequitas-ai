"""
Aequitas AI — Eval harness, step 1: build the question set.

Methodology adapted from Dahl et al. (2024)'s reference-based task
definitions (Court, Citation, Quotation), applied to real cases sampled
from our own ingested corpus rather than Dahl's original HuggingFace
dataset — see docs/DECISIONS.md, 2026-09-28 entries, for why.

Samples N real cases per task from Supabase (requiring the fields each
task needs to have a checkable ground truth), builds one natural-language
question per case, and writes everything needed for both running and
scoring to a single JSON file.

Run: python3 build_eval_questions.py
"""

import json
import os
import random

from supabase import create_client

N_PER_TASK = 25
OUTPUT_PATH = "eval_questions.json"
MIN_RAW_TEXT_LENGTH = 500  # avoid near-empty opinions for the Quotation task

random.seed(42)  # reproducible sample across runs/models


def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.")
    return create_client(url, key)


def fetch_candidate_cases(client, required_field: str | None = None) -> list[dict]:
    """Pages through cases, optionally requiring a specific field be
    non-null/non-empty, and long enough raw_text to be usable."""
    cases = []
    page_size = 500
    offset = 0
    while True:
        query = client.table("cases").select(
            "id,case_name,citation,court,jurisdiction,decision_date,raw_text"
        )
        if required_field:
            query = query.not_.is_(required_field, "null")
        resp = query.range(offset, offset + page_size - 1).execute()
        rows = resp.data or []
        if not rows:
            break
        cases.extend(r for r in rows if len(r.get("raw_text", "")) >= MIN_RAW_TEXT_LENGTH)
        offset += page_size
        if len(rows) < page_size:
            break
    return cases


def build_questions(client) -> list[dict]:
    questions = []
    used_ids: set[str] = set()

    # --- Court task ---
    court_candidates = [
        c for c in fetch_candidate_cases(client, "court") if c["id"] not in used_ids
    ]
    sample = random.sample(court_candidates, min(N_PER_TASK, len(court_candidates)))
    for c in sample:
        used_ids.add(c["id"])
        questions.append(
            {
                "question_id": f"court_{c['id']}",
                "task": "court",
                "case_id": c["id"],
                "case_name": c["case_name"],
                "citation": c["citation"],
                "query": f"What court decided the case {c['case_name']}, {c['citation']}?",
                "ground_truth": c["court"],
            }
        )

    # --- Citation task ---
    citation_candidates = [
        c for c in fetch_candidate_cases(client) if c["id"] not in used_ids
    ]
    sample = random.sample(citation_candidates, min(N_PER_TASK, len(citation_candidates)))
    for c in sample:
        used_ids.add(c["id"])
        questions.append(
            {
                "question_id": f"citation_{c['id']}",
                "task": "citation",
                "case_id": c["id"],
                "case_name": c["case_name"],
                "citation": c["citation"],
                "query": f"What is the legal citation for the case {c['case_name']}?",
                "ground_truth": c["citation"],
            }
        )

    # --- Quotation task ---
    quotation_candidates = [
        c for c in fetch_candidate_cases(client) if c["id"] not in used_ids
    ]
    sample = random.sample(quotation_candidates, min(N_PER_TASK, len(quotation_candidates)))
    for c in sample:
        used_ids.add(c["id"])
        questions.append(
            {
                "question_id": f"quotation_{c['id']}",
                "task": "quotation",
                "case_id": c["id"],
                "case_name": c["case_name"],
                "citation": c["citation"],
                "query": (
                    f"Provide a direct quotation from the court's opinion in "
                    f"{c['case_name']}, {c['citation']}."
                ),
                # ground_truth here is the full raw_text to check the produced
                # quote against at scoring time, not a single expected answer.
                "ground_truth": c["raw_text"],
            }
        )

    return questions


def main():
    client = get_client()
    questions = build_questions(client)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(questions, f, indent=2)
    by_task = {}
    for q in questions:
        by_task[q["task"]] = by_task.get(q["task"], 0) + 1
    print(f"Wrote {len(questions)} questions to {OUTPUT_PATH}: {by_task}")


if __name__ == "__main__":
    main()
