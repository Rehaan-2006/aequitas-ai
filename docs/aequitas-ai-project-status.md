# Aequitas AI — Complete Project Context & Status

This document gives any AI assistant (or new teammate) full context on this project. Read the **Current State** section for what's true right now — only open the appendix if you need the reasoning behind a past decision.

---

## Current State

**What this is:** A legal research and drafting assistant (US case law) built as a multi-agent pipeline, each stage targeting a documented cause of legal hallucination. Core claim: measures and beats hallucination rates published in Dahl et al. (2024) and Magesh et al. (2025). College major project (B.Tech CSE, GRIET, Hyderabad) also being shaped as a YC-style pitch.

**Tech stack (confirmed):**
- Agents/orchestration: PydanticAI · LLM provider: Openrouter
- Vector DB: Supabase + pgvector (HNSW index) · Embeddings: `BAAI/bge-base-en-v1.5`, local via sentence-transformers, query-side instruction prefix (`"Represent this sentence for searching relevant passages: "`) required
- Sparse search: Postgres full-text search (tsvector + GIN index) — no separate search engine
- Backend: FastAPI · Frontend: React (Vite) + Tailwind CSS
- Corpus: `common-pile/caselaw_access_project`, 502 cases (Fifth & Ninth Circuits)
- Benchmark: Dahl et al. (2024), HuggingFace `reglab/legal_hallucinations`
- Hosting: backend on Render/Fly.io (Docker), frontend on Vercel, DB/auth/storage on Supabase

**Current schema:**
- `cases` — `id` (UUID), `citation` (TEXT UNIQUE), `case_name`, `court`, `jurisdiction`, `decision_date`, `raw_text`, `is_overruled`, `overruled_by` (UUID, FK to `cases`, nullable), `source`, `created_at`.
- `case_chunks` — `case_id` (FK, cascade delete), `chunk_index`, `chunk_text`, `embedding` (`vector(768)`).
- `case_citations` — surrogate `id` PK, `citing_case_id`, `cited_case_id`, `cited_citation_text`.
- Search RPCs: `match_case_chunks` (dense, cosine similarity) and `keyword_search_case_chunks` (sparse, full-text on `chunk_text`) — both in `backend/app/db/migrations/`.
- `is_overruled` is currently synthetic (~10% deterministically flagged via hash of case name, since the CAP dataset has no real Shepard's/KeyCite data) — see Known Open Items.

**Modules complete:**
- **Module 0** — repo/environment scaffolding (backend + frontend). Complete.
- **Module 1** — Data infrastructure: corpus ingestion, embedding pipeline, vector DB, `match_case_chunks` RPC, citation graph (`case_citations`, 7,283 edges via `scripts/build_citations.py`). Complete. Post-completion tech debt (citation regex tightening + idempotency, `overruled_by` column restored, CI integration-marker fix) also done.
- **Module 1.5** — Input & Sanitization Layer (`backend/app/services/sanitizer.py`). Complete. Single entry point `sanitize_query(query: str)` for all pipeline entry points (research, drafting, document audit), three checks: PII detection/redaction (regex — SSN, email, phone, credit card), prompt-injection detection, and scope check (legal vs. off-topic). Scope and injection checks were rebuilt mid-module from keyword/regex lists to embedding similarity (nearest-neighbor cosine, not mean, against cached reference query sets) using the same `bge-base-en-v1.5` model as retrieval, via the new shared `embedding_service.py` singleton — one model dependency instead of two. Two real bugs caught and fixed during review: a bare 9-digit regex was false-flagging EINs/case numbers as SSNs (removed), and legal queries containing an off-topic word were being misclassified (fixed by the embedding rewrite). Structured output via `SanitizationResult` (status/query/reason/redacted query) and a `SanitizationStatus` enum (PASSED, PII_DETECTED, INJECTION_DETECTED, OFF_TOPIC). 28/28 tests pass (26 sanitizer + 2 pre-existing), including regression tests for both fixed bugs. CI updated to cache the Hugging Face model directory (`~/.cache/huggingface`) since sanitizer tests now load a local ML model in CI (a few seconds, no live credentials, still outside the integration marker).
- **Module 2** — Query Analyzer Agent. Complete — live-tested on 5 hand-picked cases (including false-premise queries), classification confirmed correct on all 5.
- **Module 3** — Hybrid Retrieval Agent (`backend/app/services/retrieval.py`). Complete, no LLM call. Dense (via shared `embed_query()` helper) + sparse (new `keyword_search_case_chunks` RPC, indexes `case_chunks.chunk_text`) fused via Reciprocal Rank Fusion (config-driven k). Citation-graph boost applies to direct edges only among already-retrieved candidates (no traversal), config-driven weight. Jurisdiction/date filters are structured parameters, not NL-parsed. Wide retrieval (`hybrid_retrieval_top_k` = 20, config setting). Added shared `backend/app/db/supabase_client.py` singleton for this and future modules to reuse. 46/46 non-integration tests pass; 8/8 integration tests pass against live Supabase, including a real fused+boosted retrieval check on two hand-picked queries. Manual post-verification against live data caught a real bug the automated suites missed (sparse search returning 0 rows on natural-language queries, see Known Open Items and `docs/DECISIONS.md`), fixed via a follow-up migration before this was called done.
- **Module 3.5** — Reranker (`backend/app/services/reranker.py`). Complete, no LLM call, no Openrouter cost. Cross-encoder (BAAI/bge-reranker-base via sentence-transformers) that narrows Module 3's wide top-20 retrieval to final top-5 (config-driven `reranker_top_n`). Lazy-loaded singleton via lru_cache; batches all candidates in one `CrossEncoder.predict()` call for efficiency. Extended `CaseChunk` with optional `rerank_score` field (preserves existing `score`, `dense_similarity`, `sparse_rank`). 7/7 mocked unit tests pass in default CI (sort-and-narrow, config defaults, field preservation, empty handling, reordering proof, batching validation); 2/2 integration tests pass with real model on hand-picked Fourth Amendment and qualified immunity queries, confirming sensible reordering. All 53 existing tests still pass (no regressions).
- **Module 4** — Validity/Citator Agent (`backend/app/services/validity_checker.py`). Complete, no LLM call, deterministic DB lookup. Single entry point `check_validity(candidates: list[CaseChunk]) -> ValidityCheckResult`. Batch-fetches `is_overruled`/`overruled_by` for all candidate case_ids in one query (never per-candidate); per candidate, passes through unchanged if not overruled, drops it with a recorded reason if overruled with no replacement, or substitutes the replacement case's `chunk_index=0` chunk (fetched directly, no fresh vector search, also batched) if `overruled_by` is populated — falling back to drop if the replacement case unexpectedly has no representative chunk. Does not implement any "request more candidates from retrieval" logic (deferred to Module 7's orchestration layer). 6/6 mocked tests pass (all-valid passthrough, drop-with-no-substitute, substitute-with-replacement, mixed batch, empty input, replacement-missing-chunk fallback); 1/1 integration test passes against the live 502-case corpus (passthrough + drop paths only — see below). While building this, found and fixed a real live-data gap: the `overruled_by` column that `schema.sql`/`DECISIONS.md` claimed was already added to Supabase (2026-09-25) had in fact never been applied to the live database — confirmed via direct Postgres connection, then applied with explicit user confirmation. See `docs/DECISIONS.md` for both that entry and the one noting the substitution branch is currently mock-only-verified (all 502 corpus rows still have `overruled_by = NULL`).

**Next step:** Module 5 (Structured Reasoning Agent) — per the build plan in `docs/aequitas-ai-build-plan.md`. The "every 3-4 modules" architectural review checkpoint (per `CLAUDE.md`) remains due — Modules 0, 1, 1.5, 2, 3, 3.5, 4 are now done; next checkpoint after Module 6 or 7 as previously noted.

**Known open items (deferred, not blockers):**
- Injection detection (Module 1.5) is embedding-based against a fixed reference set — catches rephrasings of known examples but isn't adversarially robust; this is flagged as a soft spot on one of the project's core invariants ("no query bypasses sanitization"), not a solved problem. Logged in `docs/DECISIONS.md`.
- Scope/injection classification uses nearest-neighbor argmax between two clusters with no "neither / low confidence" outcome — an ambiguous query is always forced into one bucket or the other based on which reference example is marginally closer, rather than being flagged as uncertain. Not yet an observed failure, but the first place to look if a false-negative injection is ever found in testing.
- `is_overruled` is synthetic (~10% hash-flagged) — real Shepard's/KeyCite data isn't in the CAP dataset. Full re-ingestion with real data is planned before final benchmarking (Module 16), not before.
- `overruled_by` is `NULL` for all rows — no automated way to populate yet; Module 4 should use it when populated but fall back to dropping the case when `NULL`.
- Citations are synthetic identifiers (CAP doesn't have real Bluebook citations) — flagged as the largest architectural risk for Module 6 (Citation Verifier). Deliberately deferred until the pipeline works end-to-end; may need a CourtListener backfill before Module 16 benchmarking.
- Corpus is 502 cases (Fifth & Ninth Circuits only) — expanding size/circuit coverage is open, to revisit before final benchmarking.
- Module 3's sparse search initially returned 0 rows for any realistic multi-word query due to AND-only `plainto_tsquery` semantics — found via manual verification against live Supabase (not by either test suite, since mocks don't exercise the real SQL and the integration tests only assert `len(results) > 0`, which dense-only output satisfied). Fixed in `0003_fix_keyword_search_or_semantics.sql`; see `docs/DECISIONS.md`. The underlying blind spot remains: no automated test currently fails if sparse search silently contributes nothing. Worth adding an integration assertion (e.g. `sparse_rank` is non-`None` for at least some results on a known query) before this pattern repeats in a later module.

---

## Appendix: Project History

### 1. The Core Idea

**The problem:** Legal research is expensive and slow, and both general-purpose LLMs and existing commercial legal-AI tools have a serious, well-documented failure mode — they hallucinate legal citations. Documented rates: 58-88% hallucination in unaided LLMs (GPT-4/GPT-3.5/Llama2) on verifiable legal questions (Dahl et al., 2024), and 17-33% even in paid commercial tools like Lexis+ AI and Westlaw AI-Assisted Research (Magesh et al., 2025).

**The core idea:** instead of one LLM answering directly, use a pipeline of specialized agents where each stage targets a specific, documented cause of legal hallucination — sanitize the query, retrieve and rerank case law, check whether retrieved cases are still valid law, reason in a structured format, and then independently verify every citation before anything reaches the user. The system explicitly abstains ("I cannot verify this legal claim") rather than guessing when it can't confirm something.

**Why this is a real contribution, not just "add RAG":** the project's defining claim is that it measures and benchmarks its own hallucination rate directly against two published academic baselines (Dahl et al., Magesh et al.), with an ablation study showing what each pipeline stage actually contributes.

### 2. How the Idea Evolved (in order)

Full rationale, trade-offs, and context for each decision below now live as ADRs in `docs/DECISIONS.md`.

1. **Started as:** a research-only tool — "Legal Aid Research & Drafting Assistant" — but the name promised drafting while the plan only did research. Added a 6th agent (Drafting Agent) to close that gap.
2. **Scope decision — US law, not Indian law:** originally conceived as an Indian-legal-aid tool, pivoted to US case law.
3. **Product expansion rejected:** considered a full multi-module "legal operating system," kept only features that reuse existing pipeline infrastructure (trace panel, verification badge, saved threads, feedback, export, citation-audit upload).
4. **"Look alive" decision:** React (Vite) + Tailwind CSS instead of a plain HTML/CSS/JS frontend.
5. **Architecture refinement via PMA Accelerator mentor's reference:** added an Input & Sanitization Layer, a cross-encoder Reranker, and an Action Gate; deferred MCP tool integration to future scope.
6. **YC positioning decided:** target customer is solo practitioners, boutique litigation firms (2-20 lawyers), and public defender organizations — deliberately **not** free legal aid for the general public (no willingness-to-pay, unauthorized-practice-of-law liability concerns).

### 3. Pipeline Architecture (as designed)

1. **Input & Sanitization Layer** — PII filter, prompt-injection detection, scope check. Not an agent — a guardrail pass.
2. **Query Analyzer Agent** (Agent 1) — classifies query (Factual / False-Premise / Exploratory), flags false premises, handles conversation memory.
3. **Hybrid Retrieval Agent** (Agent 2) — dense + sparse search, citation-graph and jurisdiction/date filters, intentionally over-retrieves.
4. **Reranker** — cross-encoder, narrows to top 3-5 most relevant passages.
5. **Validity/Citator Agent** (Agent 3) — drops overruled/bad law, substitutes current governing case where available.
6. **Structured Reasoning Agent** (Agent 4) — strict IRAC-formatted answer, every claim traceable to a validated source.
7. **Citation Verifier Agent** (Agent 5) — independently checks every citation against retrieved source text. Outcomes: auto-answer, abstain/flag, or action gate (drafted documents only).
8. **Drafting Agent** (Agent 6, optional) — template-matched document generation from verified research, re-verified before return, gated behind user approval.
9. **Tools via MCP** — future scope only, not implemented.

A full architecture diagram (SVG), Mermaid class/sequence/ER diagrams, DFD Level 0/1, an activity diagram, and a use-case diagram have all been built for this pipeline.

### 4. Base Papers, Literature Survey & Research Plan

**Primary base paper:** Dahl, Magesh, Suzgun & Ho (2024), "Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models," *Journal of Legal Analysis* 16(1):64-93. 58-88% hallucination rates across GPT-4/3.5/PaLM-2/Llama2. Dataset: HuggingFace `reglab/legal_hallucinations`.

**Secondary base paper:** Magesh, Surani, Dahl, Suzgun, Manning & Ho (2025), "Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools," *Journal of Empirical Legal Studies* 22:216-242. Lexis+ AI 17%, Westlaw 33%, GPT-4 43% hallucination rates.

**25-paper literature survey**, 10-paper core comparative table. Key supporting references: LePhantomCite (2026, citation-injection detection methodology for the Citation Verifier eval), CLERC (2024, retrieval+RAG benchmark, ~41-48% recall@1000), LegalBench-RAG (2024), LegalBench (2023), several Indian-legal-NLP papers (ILDC, IL-TUR, IL-PCSR, InLegalBERT, IndicLegalQA — kept from the original landscape survey though the build targets US law), plus Ready Jurist One, LRAGE, LexRAG, Agentic RAG Survey, RAGTruth, GPT-4 Passes the Bar Exam, Seven Failure Points RAG, RAGAS, Self-RAG, Survey of Hallucination in NLG, Athena, CBR-RAG, Gao et al. RAG Survey, MAUD, LexGLUE, CaseHOLD, CUAD, Lewis et al. (2020) RAG paper.

**Two planned research papers:**
- **Paper 1:** gap analysis of current legal-tech tools vs. actual case-law retrieval accuracy.
- **Paper 2:** empirical evaluation of hallucination reduction vs. Dahl et al./Magesh et al. baselines, ablation study (disabling each stage), Citation Verifier precision/recall via LePhantomCite-style injected-hallucination protocol.

### 5. Artifacts Built

- Review-1 and Review-2 presentations: complete and fact-checked.
- Architecture diagram (SVG), Mermaid diagrams (class, sequence, ER, DFD L0/L1, activity, use-case-via-flowchart), Gemini image-generation prompts for each.
- Module-by-module build plan (v4, Modules 0-16, per-module "definition of done").
- Five role-specific handoff files (Frontend, Backend, Database & Data Infra, AI Engineer, Validator) with interface contracts (`run_pipeline()`, `verify_all()`).
- GitHub repository: https://github.com/Rehaan-2006/aequitas-ai.

### 6. Module 1 — Implementation Deviations from Original Plan

Real implementation work on Module 1 surfaced places where the original plan didn't survive contact with reality — all now the actual current state. Full context/rationale logged as ADRs in `docs/DECISIONS.md` (dated 2026-09-15): corpus source swapped to `common-pile/caselaw_access_project`, embedding model settled on `BAAI/bge-base-en-v1.5` (768-dim, local), pgvector indexing IVFFlat → HNSW, chunking via `RecursiveCharacterTextSplitter`, date parsing via `dateutil` fuzzy parsing, synthetic citation identifiers (real reporter citations unavailable in CAP), corpus reduced to 502 cases (Fifth & Ninth Circuits), citation graph via regex extraction (known quality caveats, later tightened), repo reorganized (`scripts/` rename, versioned schema, CI test-discovery workaround later replaced with a proper pytest marker), and confirmed schema drift (extra `cases` columns, surrogate PK on `case_citations`, `overruled_by` column later restored).

**Retrieval verified end-to-end:** live test query ("Fourth amendment unreasonable search and seizure of vehicle without warrant") correctly returned three highly relevant real case chunks at 0.80+ cosine similarity.

**Citation graph:** `scripts/build_citations.py` inserted 7,283 citation edges across the 502 cases.