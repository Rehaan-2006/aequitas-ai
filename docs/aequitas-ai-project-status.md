# Aequitas AI — Complete Project Context & Status

This document exists to give any AI assistant (or new teammate) full context on this project: what it is, why every major decision was made, what's been built so far, and where things stand right now. It does not include the module-by-module build plan — that's a separate file. This is the "why" and "what," not the "how to implement."

---

## 1. The Core Idea

**Aequitas AI** is a legal research and drafting assistant for a college major project (B.Tech CSE, GRIET, Hyderabad, Batch B7) that has evolved into something the team also wants to pitch as a real product (YC-shaped).

**The problem:** Legal research is expensive and slow, and both general-purpose LLMs and existing commercial legal-AI tools have a serious, well-documented failure mode — they hallucinate legal citations. Documented rates: 58-88% hallucination in unaided LLMs (GPT-4/GPT-3.5/Llama2) on verifiable legal questions (Dahl et al., 2024), and 17-33% even in paid commercial tools like Lexis+ AI and Westlaw AI-Assisted Research (Magesh et al., 2025).

**The core idea:** instead of one LLM answering directly, use a pipeline of specialized agents where each stage targets a specific, documented cause of legal hallucination — sanitize the query, retrieve and rerank case law, check whether retrieved cases are still valid law, reason in a structured format, and then independently verify every citation before anything reaches the user. The system explicitly abstains ("I cannot verify this legal claim") rather than guessing when it can't confirm something.

**Why this is a real contribution, not just "add RAG":** the project's defining claim is that it measures and benchmarks its own hallucination rate directly against two published academic baselines (Dahl et al., Magesh et al.), with an ablation study showing what each pipeline stage actually contributes. This is a testable, citable claim almost no competitor makes publicly.

---

## 2. How the Idea Evolved (in order)

Full rationale, trade-offs, and context for each decision below now live as ADRs in `docs/DECISIONS.md`.

1. **Started as:** a research-only tool — "Legal Aid Research & Drafting Assistant" — but the name promised drafting while the plan only did research. Added a 6th agent (Drafting Agent) to close that gap. *(See DECISIONS.md: "Add Drafting Agent as a sixth pipeline stage.")*
2. **Scope decision — US law, not Indian law:** originally conceived as an Indian-legal-aid tool, pivoted to US case law. *(See DECISIONS.md: "Target US case law instead of Indian law.")*
3. **Product expansion rejected:** considered a full multi-module "legal operating system," kept only features that reuse existing pipeline infrastructure (trace panel, verification badge, saved threads, feedback, export, citation-audit upload). *(See DECISIONS.md: "Scope down product feature set to pipeline-native features only.")*
4. **"Look alive" decision:** React (Vite) + Tailwind CSS instead of a plain HTML/CSS/JS frontend. *(See DECISIONS.md: "React (Vite) + Tailwind CSS instead of vanilla HTML/CSS/JS.")*
5. **Architecture refinement via PMA Accelerator mentor's reference:** added an Input & Sanitization Layer, a cross-encoder Reranker, and an Action Gate; deferred MCP tool integration to future scope. *(See DECISIONS.md: "Add Input & Sanitization Layer, Reranker, and Action Gate; defer MCP integration.")*
6. **YC positioning decided:** target customer is solo practitioners, boutique litigation firms (2-20 lawyers), and public defender organizations — deliberately **not** free legal aid for the general public, since that's not venture-backable (no willingness-to-pay, unauthorized-practice-of-law liability concerns). The pitch: commercial tools still hallucinate 17-33% of the time; this is the first benchmark-backed legal research and drafting system with an independently verified, published-baseline-beating accuracy rate.

---

## 3. Current Architecture (final state, as of the last diagram revision)

**Pipeline, in order:**
1. **Input & Sanitization Layer** — PII filter, prompt-injection detection, scope check (reject non-legal queries). Not an "agent" — a guardrail pass.
2. **Query Analyzer Agent** (Agent 1) — classifies the query (Factual / False-Premise / Exploratory), flags false premises, handles conversation memory.
3. **Hybrid Retrieval Agent** (Agent 2) — dense (semantic) + sparse (keyword) search over the case-law corpus, combined with citation-graph and jurisdiction/date filters. Intentionally over-retrieves (wide top-k).
4. **Reranker** — cross-encoder, narrows results to the top 3-5 most relevant passages.
5. **Validity/Citator Agent** (Agent 3) — drops any case flagged as overruled/bad law, substitutes the current governing case where available. Reads a `still_good_law`/`overruled_by` field maintained in the case-law database.
6. **Structured Reasoning Agent** (Agent 4) — produces a strict IRAC-formatted answer (Issue, Rule, Application, Conclusion), every claim traceable to a specific validated source. (An optional future optimization: splitting this into a fast model for simple lookups and a stronger reasoning model for complex queries — not required for MVP.)
7. **Citation Verifier Agent** (Agent 5) — independently checks every citation against the actual retrieved source text. Three possible outcomes: **auto-answer** (all verified), **abstain/flag** (unverifiable claim, system says so rather than guessing), or **action gate** (specifically for drafted documents — requires explicit user approval before export).
8. **Drafting Agent** (Agent 6, optional path) — template-matched, section-by-section document generation from verified research, re-verified through the Citation Verifier Agent before being returned, gated behind user approval.
9. **Tools via MCP** — shown as future scope only, not implemented.

This is a **6-agent pipeline** (the "5 agents" from the original pitch, plus the Drafting Agent added later) — the team is keeping the "Agent 1-5" numbering/naming from the original pitch for consistency in presentations, even though the diagrams now group things under functional headers.

A full architecture diagram (SVG), a Mermaid class diagram, sequence diagram, ER diagram, DFD Level 0 and Level 1, an activity diagram, and a use-case diagram have all been built for this pipeline — see Section 6.

---

## 4. Tech Stack (current, confirmed)

- **Agents/orchestration:** PydanticAI
- **Vector search:** pgvector or ChromaDB (pgvector preferred, avoids running two databases alongside Postgres)
- **Citation graph:** plain Postgres adjacency table
- **Backend API:** FastAPI
- **Database, Auth, Storage:** Supabase (Postgres + Google OAuth + file storage)
- **Frontend:** React (Vite) + Tailwind CSS — confirmed, not plain HTML/CSS/JS, not Next.js
- **LLM provider:** Openrouter (consistent with the team's other past projects)
- **Case law corpus:** Caselaw Access Project (CAP) and/or CourtListener — starting with one federal circuit, not the full corpus
- **Benchmark dataset:** Dahl et al. (2024) — HuggingFace `reglab/legal_hallucinations`
- **Document generation:** python-docx (Word), WeasyPrint (PDF)
- **Document text extraction (uploads):** pdfplumber/pypdf (PDF), python-docx (DOCX), plain read (TXT)
- **Hosting:** backend Dockerized on Render or Fly.io; frontend on Vercel; DB/auth/storage on Supabase
- **Payments/credits:** Stripe (test mode)

**Code standards the team committed to:** modular and scalable architecture (new features should be addable without rewriting existing code), no code duplication (shared logic like citation verification is built once and reused across every caller), minimal comments (only where genuinely non-obvious), and no emojis anywhere in code, commit messages, or comments.

---

## 5. Base Papers, Literature Survey & Research Plan

**Primary base paper:** Dahl, Magesh, Suzgun & Ho (2024), "Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models," *Journal of Legal Analysis* 16(1):64-93. Tested GPT-4/3.5/PaLM-2/Llama2 on ~15,000 real federal court cases via ~200K verifiable questions. Found 58-88% hallucination rates. Public dataset: HuggingFace `reglab/legal_hallucinations`. Chosen as primary target because it's an easier, clearer baseline to beat (tests generic LLMs, not RAG tools).

**Secondary base paper:** Magesh, Surani, Dahl, Suzgun, Manning & Ho (2025), "Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools," *Journal of Empirical Legal Studies* 22:216-242. Found Lexis+ AI 17%, Westlaw 33%, GPT-4 43% hallucination rates via manual expert review of preregistered queries.

**Literature survey has grown over time** — the team's actual deck now cites 25 papers total, with a 10-paper core comparative table. Beyond the two base papers, key supporting references include:
- **LePhantomCite** (Liu, Stammbach & Henderson, 2026) — citation-injection detection benchmark; methodology source for evaluating the Citation Verifier Agent (corrupt real citations, measure detection recall/F1).
- **CLERC** (Hou et al., 2024) — legal case retrieval + RAG generation benchmark; found ~41-48% retrieval recall@1000 and that stronger-sounding generations often hallucinate *more*, not less.
- **LegalBench-RAG** (Pipitone & Houir Alami, 2024) — retrieval-only evaluation benchmark, 6,858 query-answer pairs.
- **LegalBench** (Guha et al., 2023) — 162-task legal reasoning benchmark, no hallucination metric.
- Several Indian-legal-NLP papers (ILDC, IL-TUR, IL-PCSR, InLegalBERT, IndicLegalQA) — kept in the literature review even though the actual build targets US law, since they were part of the original research landscape survey.
- Additional supporting references: Ready Jurist One, LRAGE, LexRAG, Agentic RAG Survey, RAGTruth, GPT-4 Passes the Bar Exam, Seven Failure Points RAG, RAGAS, Self-RAG, Survey of Hallucination in NLG, Athena, CBR-RAG, Gao et al. RAG Survey, MAUD, LexGLUE, CaseHOLD, CUAD, and the original Lewis et al. (2020) RAG paper.

**Consistent finding across the literature review:** every existing approach solves one piece of the hallucination problem (better retrieval, better reasoning, or after-the-fact detection) but none combine them into a single verified, end-to-end, benchmarked pipeline — this gap is the direct justification for Aequitas AI's architecture.

**Two planned research papers:**
- **Paper 1:** gap analysis of current legal-tech tools vs. actual case-law retrieval accuracy.
- **Paper 2:** empirical evaluation of the pipeline's hallucination reduction vs. Dahl et al./Magesh et al. baselines, including an ablation study (disabling each pipeline stage one at a time) and the Citation Verifier's precision/recall via the LePhantomCite-style injected-hallucination protocol.

---

## 6. Artifacts Built So Far

- **Review-1 presentation:** completed (literature survey table, gaps identification, proposed approach, requirements, conclusion, references).
- **Review-2 presentation:** completed and fact-checked (architecture, requirements, DFD Level 0/1, process logic/activity diagram, ER diagram, implementation status, expanded 25-paper references list). A few leaked-content and consistency errors from early drafts were caught and corrected (mismatched gap description, wrong table header, self-contradictory status row, a stray reference fragment).
- **Architecture diagram** (SVG) — full pipeline visualization, later revised to remove the MCP block (moved to future-scope-only) and add the Drafting Agent stage explicitly.
- **Mermaid diagrams:** class diagram (agents + data model, later simplified to match the team's preferred detail level), sequence diagram (research flow + drafting/approval flow), ER diagram (7-table schema), DFD Level 0 (context diagram), DFD Level 1 (5-process detailed diagram), activity diagram (3-branch flowchart: research query / document upload / drafting request), use-case diagram (approximated via flowchart, since Mermaid has no native use-case notation).
- **Gemini image-generation prompts** — written for each of the above diagrams as an alternative rendering path, styled to match reference images the team had from other example projects.
- **Module-by-module build plan** (maintained separately, not duplicated here) — currently at v3, covering Modules 0 through 16, with explicit "definition of done" gates per module.
- **Five role-specific context/build files** — split from the master build plan into standalone handoff documents for Frontend, Backend, Database & Data Infrastructure, AI Engineer, and Validator, each with the modules relevant to that role plus explicit interface contracts between roles (e.g. `run_pipeline()`, `verify_all()`) so work can proceed in parallel without duplication.
- **GitHub repository:** created at https://github.com/Rehaan-2006/aequitas-ai. Module 0 (repo/environment scaffolding for both backend and frontend) is complete.

---

## 7. Implementation Changes & Deviations from Plan

Real implementation work on Module 1 (Data Infrastructure) surfaced several places where the original plan didn't survive contact with reality. All of these are now the actual, current state — later modules and the build plan should assume these, not the original versions. Full context, rationale, and trade-offs for each are logged as ADRs in `docs/DECISIONS.md` (dated 2026-09-15): corpus source swapped to `common-pile/caselaw_access_project`, embedding model settled on `BAAI/bge-base-en-v1.5` (768-dim, run locally), pgvector indexing switched from IVFFlat to HNSW, chunking upgraded to `RecursiveCharacterTextSplitter`, date parsing normalized via `dateutil` fuzzy parsing, synthetic citation identifiers generated in place of unavailable reporter citations, corpus size reduced to 502 cases (Fifth and Ninth Circuits only), the citation graph built via regex extraction (with known quality caveats), the repo reorganized (`scripts/` rename, versioned schema, informal CI test-discovery workaround), and confirmed schema drift (extra `cases` columns, a surrogate PK on `case_citations`, and a missing `overruled_by` column).

**Current schema, for reference (normalized relational structure in Postgres):**
- `cases` — parent case metadata: `id` (UUID), `citation` (TEXT NOT NULL UNIQUE), `case_name`, `court`, `jurisdiction`, `decision_date` (DATE), `raw_text`, `is_overruled`, plus `source` and `created_at`.
- `case_chunks` — `case_id` (FK to `cases`, `ON DELETE CASCADE`), `chunk_index`, `chunk_text`, `embedding` (`vector(768)`).
- `case_citations` — surrogate `id` primary key, `citing_case_id`, `cited_case_id`, `cited_citation_text`.
- Search runs through a Supabase SQL RPC function, `match_case_chunks`, computing cosine similarity (`1 - (case_chunks.embedding <=> query_embedding)`) and joining `case_chunks` with `cases`.

**Retrieval verified end-to-end:** a query-side instruction prefix (`"Represent this sentence for searching relevant passages: "`, per BAAI's official bge-base-en-v1.5 usage guidance — applied to the query only, never to ingested chunk text) was added to the retrieval test, and a live test query ("Fourth amendment unreasonable search and seizure of vehicle without warrant") correctly returned three highly relevant real case chunks at 0.80+ cosine similarity. Embedding model, HNSW index, and the `match_case_chunks` RPC are all confirmed working together.

**Citation graph populated:** `scripts/build_citations.py` inserted 7,283 citation edges across the 502 cases (see DECISIONS.md for extraction method and known caveats).

**Module 1 is now marked COMPLETE.** Committed and pushed to GitHub (`main` branch). Open decision for what's next: Module 1.5 (Input & Sanitization) vs. Module 2 (Query Analyzer) — not yet chosen.

---

## 8. Current Status

- Planning, architecture, literature review, database schema, tech stack, and the full module-by-module build plan (including the 5 role-specific handoff files) are **finalized**.
- The GitHub repository exists and Module 0 (scaffolding) is done.
- Module 1 (Data Infrastructure) is in progress: corpus source, embedding pipeline, vector DB, and search RPC are working end to end, with 502 cases (Fifth and Ninth Circuits) ingested and embedded — see Section 7 for the real implementation details, which deviate from the original plan in several places. Citation-graph population (`case_citations`) and expanding corpus size/circuit coverage remain open.
- Both Review-1 and Review-2 presentations are complete.
- Next step: continue Module 1 (citation graph, possibly more corpus), then move into Modules 1.5/2 onward per the (now updated) build plan.
