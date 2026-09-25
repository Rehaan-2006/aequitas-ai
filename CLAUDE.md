# Aequitas AI — Project Instructions

## What this is
A legal research and drafting assistant built around a multi-agent pipeline that sanitizes and classifies a query, retrieves and reranks case law, checks whether it's still valid, reasons in IRAC format, and independently verifies every citation before anything reaches the user. A drafting agent optionally generates legal documents from verified research, gated behind explicit user approval. The core claim of the project (and the subject of two planned research papers) is that this pipeline measures and beats the hallucination rates published in Dahl et al. (2024) and Magesh et al. (2025).

## Before doing anything else
Read these two files in full before writing any code:
- `docs/aequitas-ai-project-status.md` — full project history: why every major decision was made (scope pivots, architecture changes, tech stack choices), current final architecture, complete tech stack, literature review, and everything built so far.
- `docs/aequitas-ai-build-plan.md` — the authoritative module-by-module execution plan (v4). This is the source of truth for what to build next and in what order. Module 0 and Module 1 are marked COMPLETE.

Do not start building until you've read both. If anything in this file conflicts with those two, the two docs win — this file is just operating instructions layered on top.

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
- No duplication: shared logic (citation verification, text extraction, retrieval) is built once and reused by every caller, never copy-pasted per module.
- Minimal comments — only where something is genuinely non-obvious. Code should read clearly from naming and structure.
- No emojis anywhere — code, commit messages, comments, terminal output.
- Config over hardcoding — API keys, model names, thresholds, chunk sizes belong in environment variables.
- Every module's "definition of done" test should be a real, runnable script, not a one-off manual check.

## Quality bar — apply this before marking any module done
- Before declaring a module finished, do a self-review pass, not just "it runs": check it against that module's "definition of done" in the build plan word for word, not from memory of what you intended to build.
- Check for architectural consistency with what's already built — does this module duplicate logic that already exists elsewhere (especially citation verification, sanitization, or retrieval), does it follow the same patterns as sibling modules, does it respect the interface contracts already established between modules.
- Every 3-4 modules, do a broader pass: re-read the whole `backend/app/` (or relevant) tree and check it still looks like one coherent codebase, not a pile of independently-built modules bolted together. Flag anything that should be refactored before continuing, and ask before doing large refactors.
- When you make an implementation decision that deviates from what the build plan describes (not just fills in unspecified detail — an actual deviation), log it in `docs/DECISIONS.md` as a new dated entry (see below), don't just silently do it and move on.

## Core invariants (never violate these, no exceptions)
- Only the Citation Verifier Agent ever marks a citation "verified." No other module re-implements or shortcuts verification logic.
- No drafted document is ever exportable without an explicit user approval step (the action gate). This is a trust boundary, not a formality.
- No user query reaches any agent without first passing through the Input & Sanitization Layer.
- Shared logic (verification, sanitization, retrieval, extraction) is written once and imported everywhere it's needed — never copy-pasted into a second module "for convenience."

## Decision records
Create `docs/DECISIONS.md` if it doesn't exist yet. Every time you make (or I make) a real implementation decision that deviates from the build plan — a swapped dependency, a changed schema, a different algorithm than specified — add an entry:

```
## [YYYY-MM-DD] Short title of the decision
**Context:** what problem forced this decision
**Decision:** what was actually chosen
**Why:** the reasoning
**Trade-off:** what this costs or defers
```

This is the same pattern already used informally in `docs/aequitas-ai-project-status.md`'s "Implementation Changes" section for Module 1 — going forward, put new ones here instead, one entry per decision, rather than adding more prose to that section.

## Database migrations
Don't edit `backend/app/db/schema.sql` in place going forward. Instead:
- Create `backend/app/db/migrations/` if it doesn't exist.
- Each schema change becomes a new file: `000N_short_description.sql` (e.g. `0001_add_overruled_by.sql`), applied in order, never edited after being applied.
- Keep `schema.sql` as the current compiled state for reference/onboarding, but the migrations folder is the real history.

## First task: clear existing technical debt before touching Module 1.5 or Module 2

Three things from Module 1 need fixing first. Do these before starting new modules — they're small, well-specified, and shouldn't need my input:

1. **Tighten `scripts/build_citations.py`'s citation regex and make it idempotent.**
   - Replace the current loose `\b(\d{1,4})\s+([A-Za-z\.\s]+)\s+(\d{1,4})\b` pattern with one that only matches real reporter abbreviations (U.S., F.2d, F.3d, F.4th, F. Supp., F. Supp. 2d, S. Ct., L. Ed., L. Ed. 2d, and similar — check a Bluebook reference for the full common set).
   - Add an idempotency guard: either `TRUNCATE case_citations` at the start of each run, or check for existing `(citing_case_id, cited_citation_text)` pairs before inserting, so re-running the script never creates duplicates.
   - Re-run it against the existing 502 cases to refresh `case_citations` with cleaner data.

2. **Add the `overruled_by` column back to `cases`.**
   - `ALTER TABLE cases ADD COLUMN overruled_by UUID REFERENCES cases(id);` — update `backend/app/db/schema.sql` to match.
   - Leave it `NULL` by default; there's no automated way to populate it yet.
   - When you build Module 4 (Validity/Citator Agent) later, it should use `overruled_by` to substitute the replacement case when populated, but fall back to just dropping the overruled case when it's `NULL`. Don't block on backfilling this now — it can be populated incrementally by hand for a small verified subset later, per the original plan.

3. **Replace the CI workaround with the standard fix.**
   - Right now `backend/tests/verify_retrieval.py` is excluded from CI by being renamed away from pytest's `test_*.py` discovery pattern. Replace this with a proper pytest marker instead: rename it back to `test_retrieval.py` (or keep the current name, doesn't matter functionally), add `@pytest.mark.integration` above the test function, register the marker in `pyproject.toml` (`markers = ["integration: requires live Supabase credentials and local ML models, run manually only"]`), and update the CI workflow to run `pytest -m "not integration"` by default.

Once these three are done, commit them as their own change (not mixed into the next module's work), then move on to Module 1.5 (Input & Sanitization Layer) per the build plan.

## Tech stack quick reference
Full detail is in the status doc — this is just for fast lookup:
- Agents/orchestration: PydanticAI · LLM provider: Openrouter
- Vector DB: Supabase + pgvector (HNSW index) · Embeddings: `BAAI/bge-base-en-v1.5`, local via sentence-transformers, query-side instruction prefix required
- Backend: FastAPI · Frontend: React (Vite) + Tailwind CSS
- Corpus: `common-pile/caselaw_access_project` (502 cases, Fifth & Ninth Circuits currently)
- Benchmark: Dahl et al. (2024), HuggingFace `reglab/legal_hallucinations`
- Hosting: backend on Render/Fly.io (Docker), frontend on Vercel, DB/auth on Supabase
