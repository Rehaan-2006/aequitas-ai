# Architecture & Implementation Decision Records

This log captures every real implementation decision that deviated from (or filled in unspecified detail beyond) the build plan, plus the architecture decisions made during planning. Going forward, new entries are added here directly, per the process in `CLAUDE.md`, instead of being folded into prose in `docs/aequitas-ai-project-status.md`.

**Dating note:** entries below dated to a specific day are backed by a git commit. Entries from the pre-implementation planning phase (before the repository's history begins) have no recorded exact date; they're marked `[2026-08, pre-repo]` and ordered by best-known sequence, not a verified timestamp.

---

## [2026-08, pre-repo] Add Drafting Agent as a sixth pipeline stage
**Context:** The project was originally scoped and pitched as "Legal Aid Research & Drafting Assistant," but the planned pipeline only covered research (query analysis, retrieval, validity checking, reasoning, citation verification) — the "Drafting" half of the name had no corresponding stage.
**Decision:** Added a Drafting Agent (Agent 6) that generates legal documents from verified research, using a database of stored legal document templates/schemas so it can't hallucinate document structure. Its output is re-verified through the Citation Verifier Agent and gated behind explicit user approval before export.
**Why:** The product name and pitch promised drafting; without this stage the deliverable didn't match the claim.
**Trade-off:** Added a full agent and a template/schema database to the build plan (Module scope grew); the team kept calling it a "5-agent pipeline" in presentations for continuity even though it's actually six stages.

## [2026-08, pre-repo] Target US case law instead of Indian law
**Context:** The project was originally conceived as an Indian-legal-aid tool.
**Decision:** Pivoted to building and benchmarking against US case law (Caselaw Access Project / CourtListener corpora).
**Why:** The strongest available hallucination benchmarks and base papers (Dahl et al. 2024, Magesh et al. 2025) are US-specific — running their exact test queries against an Indian-only corpus would make the core benchmarking claim invalid. US corpora are also more readily available and pre-cleaned.
**Trade-off:** Indian-legal-NLP literature (ILDC, IL-TUR, IL-PCSR, InLegalBERT, IndicLegalQA) remains in the literature review for historical continuity even though it no longer matches the build target — a minor inconsistency between the lit review and the actual corpus.

## [2026-08, pre-repo] Scope down product feature set to pipeline-native features only
**Context:** Brainstorming explored a full multi-module "legal operating system" — Command Center, Case Binder/Graph Explorer, document redlining, etc.
**Decision:** Rejected the expanded product surface. Kept only features that reuse existing pipeline infrastructure: a trace panel, a verification badge, saved threads, thumbs up/down feedback, PDF/DOCX export, and a document-upload citation-audit feature.
**Why:** The expanded scope was too large for a 5-person, time-boxed college team project.
**Trade-off:** Defers a broader product vision (case management, graph exploration UI) to a hypothetical future phase; the MVP is deliberately narrower than what was discussed.

## [2026-08, pre-repo] React (Vite) + Tailwind CSS instead of vanilla HTML/CSS/JS
**Context:** The team's usual default for projects like this was plain HTML/CSS/JS.
**Decision:** Chose React (Vite) + Tailwind CSS for the frontend, not Next.js.
**Why:** The feature set (trace panel, verification badges, action-gate approval flow, live credits) involves too much interdependent UI state to hand-manage cleanly in vanilla JS. This decision was revisited once and confirmed to stand.
**Trade-off:** Higher frontend build complexity and tooling overhead than a static site, accepted in exchange for maintainable state management.

## [2026-08, pre-repo] Add Input & Sanitization Layer, Reranker, and Action Gate; defer MCP integration
**Context:** A reference architecture shared by the team's internship mentor (a GenAI real estate lease assistant with input sanitization, hybrid retrieval, reranking, and a supervision layer) was adapted for this project.
**Decision:** Added three things to the pipeline: (1) an Input & Sanitization Layer — PII filter, prompt-injection detection, scope check — in front of the Query Analyzer Agent; (2) a cross-encoder Reranker between the Hybrid Retriever and the Validity/Citator Agent, narrowing results to the top 3-5 passages; (3) an Action Gate requiring explicit user approval before any drafted document can be exported, as a third supervision outcome alongside auto-answer and abstain. MCP tool integration was explicitly deferred to future scope, not part of the current build.
**Why:** These closed gaps the reference architecture exposed: unsanitized input reaching agents, over-wide retrieval results reaching the reasoning stage, and no human checkpoint before an actionable document could leave the system.
**Trade-off:** Three additional architectural components to build and maintain (Modules 1.5 and reranker logic) versus the original simpler pipeline; MCP tooling is left unbuilt, so no external tool-calling capability exists yet.

---

## [2026-09-15] Replace CAP corpus source with the `common-pile` mirror
**Context:** The official Free Law Project CAP dataset on Hugging Face (`free-law/Caselaw_Access_Project`) is gated, and the access request sat in "pending" status, blocking programmatic pulls. CourtListener's own bulk data (`wiki.free.law`) was also considered, but it ships as raw PostgreSQL dumps split across multiple large CSVs (Courts, Dockets, Opinion Clusters, Opinions), requiring gigabytes of downloads and multi-table relational joins locally just to get text with metadata.
**Decision:** Switched the corpus source to `common-pile/caselaw_access_project` — an ungated, publicly accessible mirror of CAP/CourtListener data that streams without authentication as flat JSON text.
**Why:** Unblocks ingestion immediately with no auth wait and no local multi-table join work.
**Trade-off:** `common-pile` rows have sparse top-level metadata (only `author`, `license`, `url`). A regex header parser (`parse_case_header`) had to be built to extract `case_name`, `docket_number`, `court`, and `decision_date_raw` from the first 500-600 characters of raw opinion text — logic that wouldn't have been needed with CAP's structured metadata.

## [2026-09-15] Standardize on BAAI/bge-base-en-v1.5 (768-dim), run locally
**Context:** `BAAI/bge-small-en-v1.5` (384 dimensions) was considered for embedding speed, but the Supabase `case_chunks` table had already been created with a 768-dimension vector column.
**Decision:** Standardized on `bge-base-en-v1.5` (768 dimensions), run locally via `sentence-transformers` rather than through OpenAI or Openrouter.
**Why:** Matches the already-created schema without a re-migration; running locally avoids API rate limits, per-token cost, and network overhead during bulk ingestion.
**Trade-off:** Slower embedding throughput on local CPU hardware (a contributing factor in the corpus-size reduction below) versus a faster hosted embedding API.

## [2026-09-15] HNSW index instead of IVFFlat for pgvector
**Context:** The original plan specified `ivfflat (embedding vector_cosine_ops) WITH (lists = 100)`. IVFFlat requires pre-existing data to train its clustering lists and fails on an empty table.
**Decision:** Switched to an HNSW index (`CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`).
**Why:** HNSW builds dynamically with no training step and gives higher recall.
**Trade-off:** HNSW index builds are typically more memory-intensive and slower to construct at very large scale than a trained IVFFlat index — an acceptable trade at the current corpus size (502 cases), worth revisiting only if the corpus grows by orders of magnitude.

## [2026-09-15] Upgrade chunking to RecursiveCharacterTextSplitter
**Context:** Naive character slicing was the original chunking approach.
**Decision:** Adopted `langchain_text_splitters.RecursiveCharacterTextSplitter` (`chunk_size=2000`, `chunk_overlap=200`, separators `["\n\n", "\n", ".", " ", ""]`).
**Why:** Stops citations and legal terms from being cut mid-word/mid-citation, which naive slicing did not guarantee.
**Trade-off:** Adds a dependency (`langchain_text_splitters`) for what is otherwise a single-purpose utility.

## [2026-09-15] Normalize inconsistent dates with dateutil fuzzy parsing
**Context:** Raw text headers had inconsistent date formats (`Feb. 12, 1973`, `Jan. 18, 1973`, `March 1, 1973`).
**Decision:** Integrated `python-dateutil`'s `parser.parse(..., fuzzy=True)` to normalize these into proper SQL dates.
**Why:** Avoids leaving `decision_date` `NULL` for a large fraction of records, which would have degraded date-filtered retrieval.
**Trade-off:** Fuzzy parsing can silently misparse an unusual date string; no validation/audit step currently checks its output against the source text.

## [2026-09-15] Generate synthetic citation identifiers for corpus cases
**Context:** `common-pile` headers only expose docket numbers (e.g. `No. 72-1889`), not official reporter citations (e.g. `483 F.2d 1234`), but `cases.citation` is `NOT NULL UNIQUE`.
**Decision:** Generated synthetic identifiers in the form `"{docket_number} ({court} {decision_date_raw})"` to satisfy the constraint. Real reporter citations are deferred to a later backfill via the CourtListener API, planned for the Validity/Citator agent build.
**Why:** Unblocks ingestion without relaxing the uniqueness constraint or blocking on an external API integration mid-Module-1.
**Trade-off:** Synthetic identifiers aren't real Bluebook citations, so no internal citation edges can currently be detected in the citation graph (in-corpus citing/cited cases don't match reporter-format regexes) until the backfill happens.

## [2026-09-15] Reduce corpus size to 502 cases, Fifth and Ninth Circuits only
**Context:** The original plan called for 1,500-1,800 cases. Local CPU embedding (on a MacBook Air) of the full target set was taking too long.
**Decision:** Stopped ingestion at 502 cases (~4,000-5,000 embedded chunks), strictly filtered to the Fifth Circuit and Ninth Circuit.
**Why:** Kept Module 1 unblocked on a hardware/time constraint rather than stalling the whole build.
**Trade-off:** Known gap versus the original corpus size target; retrieval has been verified end-to-end against the smaller corpus and the module is otherwise complete, but this should be revisited before final benchmarking if time allows, since a smaller/narrower corpus could affect how representative the benchmark results are.

## [2026-09-15] Citation graph built via regex extraction over raw text
**Context:** `case_citations` schema was ready; citation-graph population needed an extraction method with no external citation-parsing API in scope yet.
**Decision:** `scripts/build_citations.py` regex-scans each case's raw text for reporter-citation patterns and inserts an edge row per match, using `cited_case_id = NULL` with `cited_citation_text` capturing the raw matched string whenever the cited case falls outside the corpus (the large majority, as expected for a small 2-circuit corpus). Inserted 7,283 citation edges across the 502 cases this way.
**Why:** Gets a working citation graph populated without waiting on a more precise (and more complex) citation-parsing solution.
**Trade-off:** The original extraction regex (`\b(\d{1,4})\s+([A-Za-z\.\s]+)\s+(\d{1,4})\b`) is loosely specified and may include false positives, and the script isn't idempotent — re-running it duplicates rows. **Status: resolved 2026-09-25** (commit `bebfc7e`) — replaced with a `REPORTER_ABBREVIATIONS` allowlist regex and a delete-before-insert idempotency guard, per `CLAUDE.md` "First task" item 1.

## [2026-09-15] Repo reorganization: scripts/ rename, versioned schema, informal CI workaround
**Context:** Module 1 work needed the repo layout and CI to stabilize around the corpus/embedding/vector-DB pipeline.
**Decision:** Renamed the ad hoc `data-prep/` folder to `scripts/` (with `pipeline.py` renamed to `ingest_corpus.py`), version-controlled the database schema at `backend/app/db/schema.sql` instead of leaving it only in the Supabase UI, and moved the retrieval integration test into `backend/tests/verify_retrieval.py` — named specifically so pytest's `test_*.py` discovery pattern would not pick it up in CI, since CI lacks the live Supabase credentials and heavy ML dependencies the test needs.
**Why:** Needed CI to stop failing on a test it couldn't actually run, without blocking on a more thorough CI fix mid-module.
**Trade-off:** The rename-based CI exclusion is a working but informal solution (a magic filename rather than an explicit skip marker). **Status: resolved 2026-09-25** (commit `bebfc7e`) — file renamed back to `test_retrieval.py`, decorated with a registered `@pytest.mark.integration` marker, and CI now runs `pytest -m "not integration"`, per `CLAUDE.md` "First task" item 3. Same-day follow-up: the test's model-load and Supabase-client setup were also moved inside the test function body, since they previously ran at module import time and so still executed during CI collection even with the marker filter.

## [2026-09-15] Schema drift confirmed: extra columns, surrogate PK, missing `overruled_by`
**Context:** Comparing the live Supabase schema against the original plan during Module 1 implementation surfaced several undocumented differences.
**Decision:** Accepted the live schema as ground truth going forward: `cases` has `source` and `created_at` columns beyond the original plan; `case_citations` uses its own surrogate `id` primary key rather than the originally planned composite key on `(citing_case_id, cited_case_id)` (confirmed necessary because the citation-population script inserts rows with `cited_case_id = NULL`, which a composite PK would reject since PK columns are `NOT NULL` by SQL standard); and the `overruled_by` self-referencing FK from the original plan was never added — only the boolean `is_overruled` flag exists.
**Why:** The composite-PK approach is structurally incompatible with `NULL` cited-case edges, which are the normal case for this corpus; the extra columns and missing FK were simply never reconciled against the original plan document until this review.
**Trade-off:** Without `overruled_by`, the Validity/Citator Agent (Module 4) cannot substitute a replacement case for an overruled one — it can only detect and drop overruled cases via `is_overruled`. **Status: resolved 2026-09-25** (commit `bebfc7e`, migration `0001_add_overruled_by.sql`) — column added as nullable, per `CLAUDE.md` "First task" item 2. Still unpopulated (by design); Module 4 falls back to dropping the overruled case when it's `NULL`.

## [2026-09-26] Data gap fix: `is_overruled` hardcoded false, SQL patch applied (script fix deferred)
**Context:** Manual spot-check of the `cases` table (per the Module 1 definition-of-done checklist) found zero rows with `is_overruled = true` across all 502 cases — statistically implausible for a real federal case-law corpus and a red flag ahead of building the Validity/Citator Agent (Module 4).
**Decision:** Traced the cause to `insert_case()` in `scripts/ingest_corpus.py`, which hardcodes `"is_overruled": False` on every insert — not a parsing bug, but an intentional stand-in, since the underlying `common-pile/caselaw_access_project` dataset carries no Shepard's/KeyCite-equivalent overruled-status metadata (that data is proprietary to Westlaw/Lexis). Applied a one-time SQL patch directly in Supabase, randomly flagging 50 of the 502 cases (~10%) to `is_overruled = true`, so Module 4 has synthetic "bad law" to detect and filter during development. Did **not** apply the accompanying suggestion to rewrite `insert_case()` to deterministically assign ~10% overruled status via an md5 hash of `case_name` — held off pending further review, so `ingest_corpus.py` is unchanged and will still hardcode `False` on any future re-run.
**Why:** Unblocks Module 4 development without a full re-ingestion; the corpus will be wiped and rebuilt with real data before Module 16 benchmarking regardless, so a temporary synthetic flag on the current dev corpus doesn't compromise the eventual research results.
**Trade-off:** The `is_overruled` flag for these 502 cases is now synthetic/random, not reflective of actual case status — same caveat class as the synthetic `citation` identifiers already noted for this corpus. Since the ingestion script itself wasn't changed, a future re-ingestion (e.g. corpus expansion) will regenerate an all-`False` table unless this is revisited. **Status: open** — script-level fix (deterministic or otherwise) still to be decided and applied before any re-ingestion.
