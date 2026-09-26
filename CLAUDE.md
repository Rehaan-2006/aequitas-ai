# Aequitas AI — Project Instructions

## What this is
A legal research and drafting assistant built around a multi-agent pipeline that sanitizes and classifies a query, retrieves and reranks case law, checks whether it's still valid, reasons in IRAC format, and independently verifies every citation before anything reaches the user. A drafting agent optionally generates legal documents from verified research, gated behind explicit user approval. The core claim of the project (and the subject of two planned research papers) is that this pipeline measures and beats the hallucination rates published in Dahl et al. (2024) and Magesh et al. (2025).

## Before doing anything else
Read these two files before writing any code:
- `docs/aequitas-ai-project-status.md` — read the **Current State** section at the top (tech stack, modules complete, what's next, open items). Only open the **Appendix: Project History** below it if the current task specifically needs past reasoning (e.g. why a prior module made a certain call).
- `docs/aequitas-ai-build-plan.md` — the authoritative module-by-module execution plan (v4). This is the source of truth for what to build next and in what order.

Do not start building until you've read both. If anything in this file conflicts with those two, the two docs win — this file is just operating instructions layered on top.

## Repo layout
- `backend/app/services/` — agent and pipeline logic (retrieval, sanitization, verification, etc.), one file per module, imported everywhere it's needed, never duplicated.
- `backend/app/db/supabase_client.py` — shared Supabase client singleton (lru_cache pattern). Reuse this in every module that needs a DB connection; don't instantiate a new client per module.
- `backend/app/services/embedding_service.py` — shared embedding singleton and `embed_query()` helper (handles the query-side instruction prefix). Reuse this; don't reimplement query embedding per module.
- `backend/app/db/schema.sql` — current compiled schema state, for reference only.
- `backend/app/db/migrations/` — real schema history. Each change is a new `000N_short_description.sql` file, applied in order, never edited after being applied.
- `backend/tests/` — mocked unit tests (default CI) plus `@pytest.mark.integration` tests (live Supabase + local models, run manually).
- `docs/DECISIONS.md` — dated ADR-style log of any implementation choice that deviates from the build plan.
- `docs/aequitas-ai-build-plan.md` — module-by-module source of truth for what's next.
- `docs/aequitas-ai-project-status.md` — current state + full project history.

## Commands
- Run mocked test suite (default CI): `cd backend && python3 -m pytest -m "not integration"`
- Run integration tests (live Supabase, manual only): `cd backend && python3 -m pytest -m "integration"`
- Apply a new migration: add file to `backend/app/db/migrations/`, run it against Supabase, then update `schema.sql` to match.

## How to work
- Follow the build plan module by module, in the order given. Don't skip ahead or build multiple modules in one pass.
- After finishing each module, stop. Show what you built, how it meets that module's "definition of done" from the plan, and wait for a go-ahead before starting the next one.
- Within a module, you have full autonomy: write the code, choose implementation details, refactor as needed, fix bugs you find along the way, and make any improvement you see fit. No need to check in on small stuff.
- **Ask first, don't just decide**, when something is a big decision — specifically:
  - Changing the database schema (adding/removing/renaming columns or tables)
  - Swapping a core dependency or changing anything in the confirmed tech stack
  - Changing a module's scope, an agent's responsibilities, or a boundary between modules from what the plan describes
  - Anything that costs real money beyond trivial API testing
  - Anything that would change what either research paper can claim (e.g. corpus size, benchmark methodology, what counts as a "verified" citation)
- Everything else — function structure, which helper libraries to use within the stack, cleanup refactors, adding tests — just do it, no need to ask.

## Code standards (non-negotiable)
- Modular, scalable: new features should be addable without rewriting existing code.
- No duplication: shared logic (citation verification, text extraction, retrieval, embedding, DB access) is built once and reused by every caller, never copy-pasted per module.
- Minimal comments — only where something is genuinely non-obvious. Code should read clearly from naming and structure.
- No emojis anywhere — code, commit messages, comments, terminal output.
- Config over hardcoding — API keys, model names, thresholds, chunk sizes belong in environment variables.
- Every module's "definition of done" test should be a real, runnable script, not a one-off manual check.

## Quality bar — apply this before marking any module done
- Before declaring a module finished, do a self-review pass, not just "it runs": check it against that module's "definition of done" in the build plan word for word, not from memory of what you intended to build.
- Check for architectural consistency with what's already built — does this module duplicate logic that already exists elsewhere (especially citation verification, sanitization, retrieval, or DB/embedding access), does it follow the same patterns as sibling modules, does it respect the interface contracts already established between modules.
- Every 3-4 modules, do a broader pass: re-read the whole `backend/app/` (or relevant) tree and check it still looks like one coherent codebase, not a pile of independently-built modules bolted together. Flag anything that should be refactored before continuing, and ask before doing large refactors. (Modules 0, 1, 1.5, 2, 3, 3.5, 4 are done — the next architectural review checkpoint is due after Module 6 or 7.)
- When you make an implementation decision that deviates from what the build plan describes (not just fills in unspecified detail — an actual deviation), log it in `docs/DECISIONS.md` as a new dated entry (see below), don't just silently do it and move on.

## Core invariants (never violate these, no exceptions)
- Only the Citation Verifier Agent ever marks a citation "verified." No other module re-implements or shortcuts verification logic.
- No drafted document is ever exportable without an explicit user approval step (the action gate). This is a trust boundary, not a formality.
- No user query reaches any agent without first passing through the Input & Sanitization Layer.
- Shared logic (verification, sanitization, retrieval, extraction, DB access, embedding) is written once and imported everywhere it's needed — never copy-pasted into a second module "for convenience."

## Decision records
`docs/DECISIONS.md` already exists. Every time you make (or I make) a real implementation decision that deviates from the build plan — a swapped dependency, a changed schema, a different algorithm than specified — add an entry:

```
## [YYYY-MM-DD] Short title of the decision
**Context:** what problem forced this decision
**Decision:** what was actually chosen
**Why:** the reasoning
**Trade-off:** what this costs or defers
```

## Database migrations
`backend/app/db/migrations/` already exists (currently at `0002_add_keyword_search.sql`). Each schema change becomes a new file: `000N_short_description.sql`, applied in order, never edited after being applied. Keep `schema.sql` as the current compiled state for reference/onboarding, but the migrations folder is the real history.

## Tech stack quick reference
Full detail is in the status doc — this is just for fast lookup:
- Agents/orchestration: PydanticAI · LLM provider: Openrouter
- Vector DB: Supabase + pgvector (HNSW index) · Embeddings: `BAAI/bge-base-en-v1.5`, local via sentence-transformers, query-side instruction prefix required
- Backend: FastAPI · Frontend: React (Vite) + Tailwind CSS
- Corpus: `common-pile/caselaw_access_project` (502 cases, Fifth & Ninth Circuits currently)
- Benchmark: Dahl et al. (2024), HuggingFace `reglab/legal_hallucinations`
- Hosting: backend on Render/Fly.io (Docker), frontend on Vercel, DB/auth on Supabase
