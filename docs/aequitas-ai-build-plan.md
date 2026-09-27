# Aequitas AI — Module-by-Module Implementation Plan (v4)

**Instructions for the AI implementing this:** Build this project one module at a time, in the order given. After each module, stop and let the team verify it works in isolation before starting the next one. Do not jump ahead or build multiple modules in one pass — each module has its own "definition of done" checklist at the end; treat that as a hard gate. If a later module needs something from an earlier one, assume the earlier module is already working and call into it rather than re-implementing it. Follow the code standards in Section 7 for every module, not just at the end.

**v4 change note:** Module 1 has been rewritten to match the actual implementation, which deviated from the original plan in several places (corpus source, embedding model, indexing strategy, schema). See the callouts inline. Every other module is unchanged from v3 except where it references something Module 1 now provides differently (flagged inline).

---

## 1. Project Context (read this first)

**What this is:** Aequitas AI is a legal research and drafting assistant. Instead of one LLM answering directly, a pipeline of specialized agents sanitizes and classifies the query, retrieves and reranks case law, checks whether it's still valid, reasons over it in a structured format, and independently verifies every citation before anything reaches the user — with the system explicitly abstaining ("I cannot verify this legal claim") rather than guessing when it can't confirm something. A drafting agent then optionally generates real legal documents from the verified research, using a database of stored document templates so it doesn't hallucinate document structure, with any drafted document gated behind explicit user approval before export. A separate feature lets users upload their own legal documents to get their citations audited using the same verification infrastructure.

**Why this approach:** General-purpose LLMs hallucinate legal citations 58-88% of the time on verifiable questions (Dahl et al., 2024); even commercial legal-AI tools like Lexis+ AI and Westlaw AI-Assisted Research hallucinate 17-33% of the time (Magesh et al., 2025). This project's core claim is that it measures and benchmarks its own hallucination rate against these two published academic baselines, with an ablation study showing what each agent contributes.

**GitHub repo:** https://github.com/Rehaan-2006/aequitas-ai — Module 0 and Module 1 are both complete and pushed to `main`. Repo was reorganized (see Module 1) to move ingestion/ETL scripts out of an ad hoc `data-prep/` folder into `scripts/`, and the DB schema is now version-controlled at `backend/app/db/schema.sql`.

**Team:** 5 people, split by role — Frontend, Backend, Database & Data Infrastructure, AI Engineer, Validator. See the five separate role-specific context files for role-scoped versions of this plan.

---

## 2. Final Tech Stack

- **Agents/orchestration:** PydanticAI
- **LLM provider:** Openrouter
- **Vector search: Supabase + pgvector** (confirmed — ChromaDB was dropped as an option)
- **Embeddings: `BAAI/bge-base-en-v1.5` (768 dimensions), run locally via `sentence-transformers`** — not via an API, to avoid rate limits/cost during bulk ingestion
- **Reranking:** a cross-encoder reranker (e.g. a sentence-transformers cross-encoder model, or a hosted reranking API)
- **Citation graph:** Postgres table (`case_citations`), schema ready, population is a later phase
- **Backend API:** FastAPI
- **Database, Auth, Storage:** Supabase (Postgres + Google OAuth + file storage)
- **Case law corpus: `common-pile/caselaw_access_project` on Hugging Face** — an ungated open mirror of CAP/CourtListener data (see Module 1 for why this replaced the official CAP dataset)
- **Chunking: `langchain_text_splitters.RecursiveCharacterTextSplitter`** (`chunk_size=2000`, `chunk_overlap=200`, separators `["\n\n", "\n", ".", " ", ""]`)
- **Benchmark dataset:** Dahl et al. (2024) — HuggingFace `reglab/legal_hallucinations`
- **Document generation:** python-docx (Word), WeasyPrint (PDF)
- **Document text extraction (uploads):** pdfplumber or pypdf for PDF, python-docx for DOCX, plain read for TXT
- **Frontend:** React (Vite) + Tailwind CSS
- **Hosting:** FastAPI backend in Docker on Render or Fly.io; Supabase for DB/auth/storage; frontend on Vercel
- **Payments/credits:** Stripe (test mode)

---

## 3. Feature List (what "done" looks like)

Unchanged from v3 — see Section 3 of the previous plan version if needed. Summary: landing page with stats, Google sign-in, research/chat page with trace panel and verification badges, source inspector, saved threads, thumbs up/down, drafting page with action-gate approval, document upload/citation audit, PDF/DOCX export, pricing/credits page, hosted deployment. Out of scope: multi-tenant Matters, case-file binder/graph explorer, document redlining, real-time WebSocket status, custom auth, OCR, MCP tools (future scope only), fast/reasoning-model routing split (optional future optimization).

---

## 4. Module-by-Module Build Order

### Module 0 — Repo & Environment Setup — COMPLETE
Repo live at https://github.com/Rehaan-2006/aequitas-ai, both `/backend` and `/frontend` scaffolded.

### Module 1 — Data Infrastructure — COMPLETE

**Corpus source:** The official Free Law Project CAP dataset on Hugging Face (`free-law/Caselaw_Access_Project`) is gated — the access request sat in "pending" status and blocked programmatic pulls. Switched to `common-pile/caselaw_access_project`, an ungated open mirror of CAP/CourtListener data that streams without authentication. CourtListener's own bulk data (`wiki.free.law`) was considered and rejected — it ships as raw PostgreSQL dumps across multiple large CSVs (Courts, Dockets, Opinion Clusters, Opinions), requiring gigabytes of downloads and local multi-table joins just to get text with metadata. `common-pile` gives streamable flat JSON text instead, at the cost of sparse top-level metadata (only `author`, `license`, `url`).

**Header parsing:** Because `common-pile` doesn't expose structured case metadata, a regex header parser (`parse_case_header`) extracts `case_name`, `docket_number`, `court`, and `decision_date_raw` from the first 500-600 characters of the raw opinion text.

**Date parsing:** Raw headers have inconsistent date formats (`Feb. 12, 1973`, `Jan. 18, 1973`, `March 1, 1973`). Use `python-dateutil`'s `parser.parse(..., fuzzy=True)` to normalize into SQL `DATE` values rather than leaving the column `NULL`.

**Citations:** `common-pile` headers only expose docket numbers (e.g. `No. 72-1889`), not official reporter citations (e.g. `483 F.2d 1234`). To satisfy `cases.citation NOT NULL UNIQUE`, generate synthetic identifiers: `f"{docket_number} ({court} {decision_date_raw})"`. Real reporter citations are deferred to a later backfill via the CourtListener API, planned for the Validity/Citator agent build (Module 4) — **flag this to whoever builds Module 4: don't assume `citation` is a real Bluebook-format citation yet.**

**Chunking:** use `langchain_text_splitters.RecursiveCharacterTextSplitter` (`chunk_size=2000`, `chunk_overlap=200`, separators `["\n\n", "\n", ".", " ", ""]`) — not naive character slicing, which cuts citations and terms mid-word.

**Embeddings:** `BAAI/bge-base-en-v1.5` (768 dimensions) via `sentence-transformers`, run locally (not through an API) to avoid rate limits and per-token cost during bulk ingestion. Use `bge-base`, not `bge-small` (384 dim) — the `case_chunks` table's vector column is already `vector(768)`. **Query-side note:** searches must prefix the query text with `"Represent this sentence for searching relevant passages: "` per BAAI's official usage recommendation for asymmetric retrieval — this prefix goes on the query only, never on the ingested chunk text. This was added to the retrieval test and confirmed working (0.80+ cosine similarity on real test queries).

**Vector indexing:** use **HNSW**, not IVFFlat. IVFFlat requires pre-existing data to train its clustering lists and fails on an empty table. `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)` builds dynamically with no training step and gives higher recall.

**Search:** implemented as a Supabase SQL RPC function, `match_case_chunks`, computing cosine similarity (`1 - (case_chunks.embedding <=> query_embedding)`) and joining `case_chunks` with `cases`. This is the function the Hybrid Retrieval Agent (Module 3) calls — don't reimplement retrieval logic elsewhere. **Verified end-to-end and working correctly** (see Section 6 for the live schema this actually runs against).

**Citation graph:** `case_citations` is populated — `scripts/build_citations.py` regex-scans each case's `raw_text` for standard reporter-citation patterns (e.g. `395 U.S. 762`) and inserts 7,283 edges across the 502 cases. As expected given the small 2-circuit corpus, the large majority of `cited_case_id` values are `NULL` (the cited case is external to the corpus) — `cited_citation_text` captures the raw matched string regardless. **Known quality caveats, not yet addressed:** the extraction regex is loose (matches any "number, words, number" pattern, not just verified reporter formats) so some noise is likely in the 7,283 edges; the script is not idempotent (no dedup guard — re-running it will insert duplicate rows); and no internal edges (a case in the corpus citing another case also in the corpus) can currently be detected, since the corpus's own `citation` values are synthetic and won't match the regex's reporter-format extraction.

**Current corpus status:** ingestion stopped at **502 cases** (~4,000-5,000 embedded chunks), strictly filtered to the **Fifth Circuit** and **Ninth Circuit**, versus an original target of 1,500-1,800 cases — local CPU embedding on a MacBook Air was too slow to hit the full target before other modules needed to unblock. **Revisit corpus size before final benchmarking** — a 502-case, 2-circuit corpus may not give a representative hallucination-rate comparison against Dahl et al.'s much larger test set.

**Repo structure (reorganized, committed and pushed):**
```
aequitas-ai/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   │   ├── schema.sql        # version-controlled source of truth for the DB schema
│   │   │   └── supabase.py
│   │   ├── services/
│   │   └── main.py
│   ├── tests/
│   │   ├── test_health.py               # boilerplate FastAPI health check, run by CI
│   │   └── verify_retrieval.py          # live Supabase + embedding-model integration check, run manually only (renamed from test_retrieval.py so CI/pytest doesn't try to auto-run it without live credentials)
│   ├── pyproject.toml
│   └── uv.lock
├── scripts/                       # renamed from data-prep/ — batch/ETL jobs
│   ├── ingest_corpus.py           # renamed from pipeline.py
│   └── build_citations.py
├── frontend/
├── docs/
├── .env                            # gitignored
├── .env.example                    # committed template
├── .gitignore
└── README.md
```

**Open work remaining before this is fully closed out:**
- Fix the citation-extraction regex to match real reporter-citation formats only (currently overly permissive), and add a dedup/idempotency guard to `build_citations.py`.
- Decide whether to expand corpus size/circuit coverage before Module 16 benchmarking, and whether to backfill real reporter citations via CourtListener before then too.
- The original plan's `overruled_by` self-referencing FK (for the Validity/Citator Agent to substitute a replacement case) is **not in the actual schema** — only a boolean `is_overruled` flag exists on `cases`. Decide with whoever builds Module 4 whether "substitute the overruling case" is still in scope, or whether the agent should just drop overruled cases without substitution for now.
- The CI fix (renaming `test_retrieval.py` → `verify_retrieval.py` so pytest stops auto-discovering it) works, but is a workaround rather than the standard fix — the more conventional approach is a `pytest.ini`/`pyproject.toml` marker (`@pytest.mark.integration`) with CI configured to skip that marker by default, which keeps the file runnable via `pytest` locally too. Not urgent, but worth doing properly before more integration tests accumulate.
- PDF/DOCX rendering deferred from Module 9's `/export` endpoint; build as a small standalone task before Module 12 (Frontend: Drafting Page), Haiku-appropriate.

**Definition of done:** met. `match_case_chunks` returns correct, highly relevant top-k chunks with case metadata (verified live), `is_overruled` correctly reflects hand-verified test cases, and the citation graph has real (if imperfect) data populated.

### Module 1.5 — Input & Sanitization Layer
- Standalone component sitting in front of every agent — every raw user query (research or drafting) passes through this before anything else runs.
- Checks: PII detection (flag/redact obvious personal data in the query), prompt-injection pattern detection, scope check (reject queries that aren't legal-research-related with a clear message).
- Build as a single reusable function/service, not duplicated per entry point (research, drafting, document audit all call it).
- **Definition of done:** Given a test set of ~15 queries (a mix of normal legal questions, an injection attempt, a query containing obvious PII, and an off-topic query), the layer correctly flags/rejects the problem cases and passes the normal ones through unchanged.

### Module 2 — Query Analyzer Agent
- Input: sanitized user question (post-Module 1.5). Output: classification (Factual / False-Premise / Exploratory) + a flag if the question assumes something false.
- Standalone PydanticAI agent, tested with a hardcoded set of test queries (include a few deliberately false-premise ones).
- **Definition of done:** Given 10 test queries (mix of valid and false-premise), the agent correctly flags the false-premise ones, verified manually, with no dependency on later modules.

### Module 3 — Hybrid Retrieval Agent
- Input: a query (post-analysis). Output: ranked list of case-law chunks, combining semantic search (calls Module 1's `match_case_chunks` RPC) with the citation graph (once `case_citations` is populated) and metadata filters (jurisdiction, date).
- **Note (v4):** semantic search is a direct call to `match_case_chunks`, not a generic "vector store query" — use the real function signature Module 1 exposes.
- **Definition of done:** Given a test query with a known "correct" case in the 502-case corpus subset, the agent retrieves it in the top-k (wider, e.g. top-20) results. This module intentionally over-retrieves — narrowing to the final top-k happens in Module 3.5.

### Module 3.5 — Reranker
- Input: the Hybrid Retrieval Agent's wider result set (e.g. top-20). Output: narrowed to the top 3-5 passages actually worth passing downstream, reordered by a cross-encoder relevance score rather than raw retrieval score.
- Keep this as a separate, swappable component — it should be possible to disable it and fall back to raw retrieval order, since this is also useful for the ablation study.
- **Definition of done:** Given a test query where the "correct" passage is retrieved but not ranked first by Module 3, the reranker correctly promotes it into the top 3-5.

### Module 4 — Validity/Citator Agent
- Input: the Reranker's output. Output: same list, minus any case flagged `is_overruled = true` in Module 1's `cases` table.
- **Note (v4):** the schema currently has only a boolean `is_overruled` flag, not the originally planned `overruled_by` FK to a replacement case — confirm with Module 1's owner whether "substitute the overruling case" is still planned before building that part; if not, this agent should just drop overruled cases for now.
- **Definition of done:** Given a reranked result set that includes a known-overruled test case (`is_overruled = true`), this agent removes it from the output list.

### Module 5 — Structured Reasoning Agent
- Input: validated case list + original query. Output: a strict Pydantic-modeled IRAC structure (Issue, Rule, Application, Conclusion), where every claim in Rule/Application must reference a specific item from the input case list (no free-floating claims).
- **Definition of done:** Output validates against the Pydantic schema every time (test with 10+ queries), and every citation in the output can be traced back to an item in the input list.
- **Optional future optimization (not required for MVP):** fast/cheap model for simple lookups, stronger reasoning model for complex queries, routed by the Query Analyzer Agent's classification.

### Module 6 — Citation Verifier Agent
- Input: the IRAC output + the original source chunks. Output: each citation marked verified/flagged, with unverifiable claims either stripped or the system abstaining on that specific claim.
- **Note (v4):** since `cases.citation` values are currently synthetic (docket-number-based, not real Bluebook citations — see Module 1), this agent's "does the citation exist" check should verify against the corpus's synthetic identifiers for now, not assume Bluebook formatting. Revisit once real citations are backfilled.
- Design this as a reusable component from the start — Module 9.5 (document upload) and Module 8 (drafting) both call this same component.
- **Definition of done:** Run against the LePhantomCite-style test (take a few real citations, deliberately corrupt/swap them, feed through) — the agent must catch the corrupted ones. Log precision/recall on a small hand-built test set.

### Module 7 — Research Pipeline Orchestration
- Wire Modules 1.5-6 together sequentially via PydanticAI, with a shared state object.
- Handle failure states explicitly: sanitization rejects the query → return the rejection reason immediately; zero retrieval results → short-circuit with a clear message; verifier flags everything → return an abstention message, not a broken answer.
- Add pipeline-trace logging here — feeds the "how this was built" UI panel and the ablation study.
- **Definition of done:** A single function `run_pipeline(query, jurisdiction, deps)` takes a raw query string and returns a complete, verified IRAC response plus a trace log.

### Module 8 — Drafting Agent (Template-Constrained, with Action Gate)
- Set up `document_templates` table and populate with 2-3 real templates.
- Template Matcher → Section-by-Section Drafter → Citation Injector → re-run through Module 6 before returning.
- **Action gate:** drafted document returned with status `pending_review`, only exportable after explicit user approval.
- **Definition of done:** Given a completed research thread, produces a document matching the template's required sections, all citations re-verified, status `pending_review`, no unfilled placeholders.

### Module 9 — Backend API (FastAPI)
- Auth: Supabase Google OAuth, verify JWT on protected routes.
- Endpoints: `POST /research`, `POST /draft`, `POST /draft/{id}/approve` / `.../reject`, `GET /threads`, `POST /threads/{id}/feedback`, `GET /threads/{id}/export`, credits middleware on `/research`, `/draft`, `/verify-document`.
- **Definition of done:** All endpoints testable via curl/Postman with a real Supabase-authenticated user, correctly enforcing credit balance, blocking export of unapproved drafts, returning real pipeline output.

### Module 9.5 — Document Upload & Citation Audit
- Pipeline: user document (PDF/DOCX/TXT) → sanitization → text extraction → citation extraction → parallel verification (via Module 6) → audit report.
- Extraction guardrails, page/size limits, scanned-PDF rejection — unchanged from v3.
- **Definition of done:** Given a test document with real/corrupted/overruled citations, returns a correct per-citation status report for a ~15-page document in well under a minute.

### Module 10 — Frontend: Auth + Shell
Unchanged from v3 — landing page with stats, Google sign-in, app shell/navigation.

### Module 11 — Frontend: Research/Chat Page
Unchanged from v3 — chat interface, trace panel, verification badge, source inspector, feedback, saved threads.

### Module 12 — Frontend: Drafting Page
Unchanged from v3 — thread → template → draft preview → approve/reject → export.

### Module 13 — Frontend: Document Audit Page
Unchanged from v3 — upload widget, audit report view reusing Module 11's components.

### Module 14 — Pricing / Credits
Unchanged from v3 — pricing page, Stripe test mode, credit balance display.

### Module 15 — Deployment
Unchanged from v3 — backend Dockerized on Render/Fly.io, frontend on Vercel.

### Module 16 — Evaluation & Benchmarking
- Run Dahl et al. (2024) benchmark questions through Module 7, log hallucination rate.
- **Note (v4):** run this against the current 502-case, two-circuit corpus first to validate the pipeline works, but treat results as preliminary — revisit corpus size (see Module 1) before treating any number here as the final reported result.
- Ablation study: disable Module 6, then also Module 4, then also Module 3.5, re-run a subset, measure hallucination rate at each step.
- LePhantomCite-style injected-citation test against Module 6, report precision/recall/F1.
- **Definition of done:** A results table showing baseline vs. full pipeline vs. each ablation step, ready for Paper 2.

---

## 5. Demo Flow

Unchanged from v3: loaded query with an overturned precedent or false premise → live trace showing the Validity/Citator Agent catching it → short motion draft in IRAC style with verified pincites and the action-gate approval step → document audit catching an injected bad citation.

---

## 6. Database Schema (Postgres / Supabase) — updated to match actual implementation

```sql
-- Case law metadata (Module 1 — ACTUAL, final schema)
CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    citation TEXT NOT NULL UNIQUE,   -- currently synthetic: "{docket_number} ({court} {decision_date_raw})"
    case_name TEXT NOT NULL,
    court TEXT,
    jurisdiction TEXT,
    decision_date DATE,              -- parsed via python-dateutil fuzzy parsing
    is_overruled BOOLEAN DEFAULT false,
    raw_text TEXT NOT NULL,
    source TEXT DEFAULT 'CAP',
    created_at TIMESTAMPTZ DEFAULT now()
    -- NOTE: no overruled_by FK (see Module 1 open work / Module 4 note above)
);

-- Case chunks with embeddings (Module 1 — final schema)
CREATE TABLE IF NOT EXISTS case_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(768),           -- BAAI/bge-base-en-v1.5
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS case_chunks_embedding_hnsw_idx ON case_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_case_chunks_case_id ON case_chunks (case_id);
-- Search: via the match_case_chunks() RPC function (cosine similarity) — see below

-- Citation graph (Module 1 — populated, 7,283 rows)
CREATE TABLE IF NOT EXISTS case_citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),   -- surrogate PK; NOT the composite key originally planned
    citing_case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
    cited_case_id UUID REFERENCES cases(id) ON DELETE SET NULL,   -- nullable; NULL for the large majority of rows (cited case is external to the 502-case corpus)
    cited_citation_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_case_citations_citing ON case_citations (citing_case_id);
CREATE INDEX IF NOT EXISTS idx_case_citations_cited ON case_citations (cited_case_id);

-- match_case_chunks RPC (Module 1 — live and verified working)
CREATE OR REPLACE FUNCTION match_case_chunks (
  query_embedding VECTOR(768),
  match_threshold FLOAT DEFAULT 0.5,
  match_count INT DEFAULT 5
)
RETURNS TABLE (
  id UUID, case_id UUID, chunk_index INT, chunk_text TEXT, similarity FLOAT,
  case_name TEXT, citation TEXT, court TEXT, decision_date DATE
)
LANGUAGE sql STABLE
AS $$
  SELECT
    case_chunks.id, case_chunks.case_id, case_chunks.chunk_index, case_chunks.chunk_text,
    1 - (case_chunks.embedding <=> query_embedding) AS similarity,
    cases.case_name, cases.citation, cases.court, cases.decision_date
  FROM case_chunks
  JOIN cases ON cases.id = case_chunks.case_id
  WHERE 1 - (case_chunks.embedding <=> query_embedding) > match_threshold
  ORDER BY case_chunks.embedding <=> query_embedding
  LIMIT match_count;
$$;
```

The above is the actual live schema (`backend/app/db/schema.sql`, version-controlled). The tables below are still as originally planned and not yet built.

```sql
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    jurisdiction VARCHAR(100) NOT NULL,
    category VARCHAR(100) NOT NULL,
    structure_schema JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Research threads (Module 9/11)
CREATE TABLE research_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    query TEXT NOT NULL,
    result_json JSONB NOT NULL,
    trace_json JSONB NOT NULL,
    feedback SMALLINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Drafted documents (Module 8/12)
CREATE TABLE legal_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID REFERENCES research_threads(id),
    template_id UUID REFERENCES document_templates(id),
    content_json JSONB NOT NULL,
    verification_status VARCHAR(50) DEFAULT 'unverified',
    approval_status VARCHAR(50) DEFAULT 'pending_review',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Uploaded document audits (Module 9.5/13)
CREATE TABLE document_audits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(10) NOT NULL,
    audit_result_json JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Credits (Module 9/14)
CREATE TABLE user_credits (
    user_id UUID PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 10,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 7. Code Standards (apply to every module, not just at the end)

- **Modularity:** each agent/stage must be a self-contained unit with a clear input/output contract (typed via Pydantic models), callable independently of the orchestration layer.
- **Reusability over duplication:** shared logic (sanitization, citation verification, text extraction, PDF/DOCX export, `match_case_chunks` retrieval) is written once and imported wherever needed.
- **Scalability for future features:** design pipeline orchestration so a stage can be inserted, swapped, or disabled (ablation study, reranker fallback) without rewriting other stages.
- **Separation of concerns:** keep API route handlers thin — business logic belongs in service modules.
- **Configuration over hardcoding:** API keys, model names, credit costs, file-size limits, embedding model name, chunk size belong in config/environment variables.
- **Naming and structure:** one clear folder per module/domain rather than a flat file dump.
- **Comments:** minimal — only where genuinely non-obvious. No emojis anywhere in code, commit messages, or comments.
- **Error handling:** every external call should fail explicitly and predictably, not silently swallow errors.
- **Testing discipline:** keep each module's "definition of done" test as an actual runnable script.

---

## 8. Base Papers & Datasets

- **Dahl, Magesh, Suzgun & Ho (2024)**, "Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models," *Journal of Legal Analysis* 16(1):64-93. Dataset: HuggingFace `reglab/legal_hallucinations`.
- **Magesh, Surani, Dahl, Suzgun, Manning & Ho (2025)**, "Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools," *Journal of Empirical Legal Studies* 22:216-242.
- **Liu, Stammbach & Henderson (2026)**, "Who Checks the Citations? Benchmarking Legal Hallucination Detection" (LePhantomCite), arXiv:2606.21155.
- **Corpus:** `common-pile/caselaw_access_project` (HuggingFace) — open mirror of CAP/CourtListener data.

---

## 9. Future Scope — MCP Tool Integration (not part of this build)

Unchanged from v3 — deferred until Modules 0-16 are complete and stable.

---

## 10. YC Positioning

Unchanged from v3 — target customer: solo practitioners, boutique litigation firms (2-20 lawyers), public defender organizations. Core value proposition: benchmark-backed citation accuracy vs. published baselines.

---

## 11. Notes for Whoever Implements This

- Module 6 (Citation Verifier) is still the hardest and most important piece — and now also needs to account for synthetic citations until the real-citation backfill happens (see Module 1/6 notes above).
- Before Module 16's benchmark numbers go into the paper as final results, revisit the 502-case/2-circuit corpus size — it's enough to unblock development but may not be representative enough for a defensible comparison against Dahl et al.'s full-scale test set.
- The `overruled_by` FK / case-substitution behavior from the original plan isn't in the schema yet — decide explicitly (Module 1 owner + Module 4 owner) whether to add it or scope Module 4 down to "drop, don't substitute" for now, rather than letting it default silently.
- Keep the credits/pricing system in test mode throughout.
- Apply Section 7's code standards from Module 0 onward.
