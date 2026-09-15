# Aequitas AI — Module-by-Module Implementation Plan (v3)

**Instructions for the AI implementing this:** Build this project one module at a time, in the order given. After each module, stop and let the team verify it works in isolation before starting the next one. Do not jump ahead or build multiple modules in one pass — each module has its own "definition of done" checklist at the end; treat that as a hard gate. If a later module needs something from an earlier one, assume the earlier module is already working and call into it rather than re-implementing it. Follow the code standards in Section 7 for every module, not just at the end.

---

## 1. Project Context (read this first)

**What this is:** Aequitas AI is a legal research and drafting assistant. Instead of one LLM answering directly, a pipeline of specialized agents sanitizes and classifies the query, retrieves and reranks case law, checks whether it's still valid, reasons over it in a structured format, and independently verifies every citation before anything is shown to the user — with the system explicitly abstaining ("I cannot verify this legal claim") rather than guessing when it can't confirm something. A drafting agent then optionally generates real legal documents (motions, demand letters, etc.) from the verified research, using a database of stored document templates so it doesn't hallucinate document structure, with any drafted document gated behind explicit user approval before export. A separate feature lets users upload their own legal documents to get their citations audited using the same verification infrastructure.

**Why this approach:** General-purpose LLMs hallucinate legal citations 58–88% of the time on verifiable questions (Dahl et al., 2024); even commercial legal-AI tools like Lexis+ AI and Westlaw AI-Assisted Research hallucinate 17–33% of the time (Magesh et al., 2025). This project's core claim, and the thing that differentiates it from "yet another legal chatbot," is that it measures and benchmarks its own hallucination rate against these two published academic baselines, with an ablation study showing what each agent contributes.

**Architecture reference:** The pipeline shape (sanitization → query understanding → tools layer → hybrid retrieval + reranking → grounded generation → supervision layer with graded outcomes) is adapted from a reference architecture pattern shown by a PMA Accelerator mentor, tailored to this project's legal-research domain. See the accompanying `aequitas-ai-architecture.svg` diagram for the visual layout.

**Scope of this build:** This plan covers building a real, hostable, demoable product (for both a university project review and a YC-style pitch) — not just a research script. It includes a proper frontend (landing page, auth, chat, drafting, document audit, pricing/credits), not a bare single-page app.

**Team:** 5 people, CS students, comfortable with Python (FastAPI, PydanticAI, LangChain-adjacent tools), some frontend experience. Backend-first team — the frontend should be kept simple to build/maintain, not necessarily simple-looking.

---

## 2. Final Tech Stack

- **Agents / orchestration:** PydanticAI
- **Vector search:** ChromaDB or pgvector (pick one — pgvector recommended if already using Postgres, to avoid running two databases)
- **Reranking:** a cross-encoder reranker (e.g. a sentence-transformers cross-encoder model, or a hosted reranking API) — pick whichever is cheapest to self-host given the team's compute
- **Citation graph:** Plain Postgres adjacency table (no dedicated graph DB needed at this scale)
- **Backend API:** FastAPI
- **Database, Auth, Storage:** Supabase (Postgres + built-in auth incl. Google OAuth + file storage)
- **Case law corpus:** Caselaw Access Project (CAP) and/or CourtListener — start with one federal circuit, not the full corpus
- **Benchmark dataset:** Dahl et al. (2024) — HuggingFace `reglab/legal_hallucinations`
- **Document generation:** python-docx (Word), WeasyPrint or a similar HTML-to-PDF tool (PDF)
- **Document text extraction (uploads):** pdfplumber or pypdf for PDF, python-docx for DOCX, plain read for TXT
- **Frontend:** React (Vite) + Tailwind CSS — not Next.js unless the team already knows it; not raw HTML/CSS/JS given the "look alive" requirement
- **Hosting:** FastAPI backend in Docker on Render or Fly.io; Supabase for DB/auth/storage; frontend on Vercel
- **Payments/credits:** Stripe (test mode is fine for the demo/pitch; doesn't need to be production-ready for YC)

---

## 3. Feature List (what "done" looks like)

1. Landing/home page with product explanation + live-ish stats (e.g., citations verified, hallucination rate vs. baseline)
2. Login page with Google sign-in (via Supabase Auth)
3. Research/chat page — ask a legal question, get an IRAC-formatted, citation-verified answer
4. Input sanitization on every query (PII filter, prompt-injection detection, scope check rejecting non-legal queries) before it reaches any agent
5. "How this answer was built" trace panel — shows what each agent/stage did (sanitization result, retrieved N cases, reranked to top-k, filtered M as bad law, verified/flagged citations)
6. Per-answer verification badge (e.g., "7/8 citations verified")
7. Source inspector — click a citation, see the underlying case text snippet
8. Saved research threads — flat history list per user, not a full multi-tenant workspace
9. Thumbs up/down feedback on answers (also doubles as eval data for the research paper)
10. Drafting page — turn a research thread into a drafted legal document using the template registry, with an explicit approve/reject step before export ("action gate")
11. Document upload & citation audit — user uploads a PDF/DOCX/TXT brief, gets a report of which citations are verified, flagged, or overruled
12. Export research memos and approved drafted documents to PDF/DOCX
13. Pricing page with a credits system (e.g., N free credits, credits consumed per research query / per draft / per document audit)
14. Hosted, working deployment (not just localhost)

**Explicitly out of scope for this build** (documented as roadmap only, do not implement): multi-tenant "Matters" workspace, full case-file binder + citation graph explorer UI, redlining/clause-risk analysis of uploaded documents (only citation auditing is in scope), real-time WebSocket agent-status streaming (use polling instead), custom-built auth (use Supabase's), OCR for scanned/image-only PDFs, MCP tool integrations (see Section 9 — future scope only), fast-model/reasoning-model routing split (optimization, see Module 5 note).

---

## 4. Module-by-Module Build Order

### Module 0 — Repo & Environment Setup
- Monorepo with `/backend` (FastAPI) and `/frontend` (React+Vite) folders, or two separate repos — team's choice, but decide before Module 1.
- `.env` handling for: Supabase URL/keys, LLM provider API key, Stripe test keys (later).
- Docker Compose for local dev: FastAPI service + Postgres (or point straight at Supabase for simplicity, skipping local Postgres).
- **Definition of done:** `docker compose up` (or equivalent) runs an empty FastAPI app that returns `{"status": "ok"}` on `/health`, and the frontend dev server runs and shows a blank page. Nothing else.

### Module 1 — Data Infrastructure
- Download a manageable subset of CAP or CourtListener data (one federal circuit, not the full corpus).
- Chunk case text (paragraph-level or semantic chunking).
- Generate embeddings and store in pgvector/ChromaDB.
- Build the citation adjacency table in Postgres: `case_id`, `cites_case_id`, plus metadata columns (`date`, `jurisdiction`, `overruled_by`, `still_good_law` boolean).
- Write a one-off script to populate `overruled_by`/`still_good_law` for at least a small hand-verified test set (full automation of this is hard — a curated subset is fine for the demo and the paper).
- **Definition of done:** Given a raw case-law text query, a script can return the top-k semantically similar chunks AND correctly report whether the source case is flagged as overruled, using nothing but this module's code. No agent framework involved yet.

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
- Input: a query (post-analysis). Output: ranked list of case-law chunks, combining semantic search (Module 1's vector store) with the citation graph (boost/deprioritize based on citation relationships) and metadata filters (jurisdiction, date).
- **Definition of done:** Given a test query with a known "correct" case in the corpus subset, the agent retrieves it in the top-k (wider, e.g. top-20) results. Runs standalone, output is inspectable as a plain list. This module intentionally over-retrieves — narrowing to the final top-k happens in Module 3.5.

### Module 3.5 — Reranker
- Input: the Hybrid Retrieval Agent's wider result set (e.g. top-20). Output: narrowed to the top 3–5 passages actually worth passing downstream, reordered by a cross-encoder relevance score rather than raw retrieval score.
- Keep this as a separate, swappable component — it should be possible to disable it and fall back to raw retrieval order, since this is also useful for the ablation study.
- **Definition of done:** Given a test query where the "correct" passage is retrieved but not ranked first by Module 3, the reranker correctly promotes it into the top 3–5. Measure this against a small hand-labeled relevance test set.

### Module 4 — Validity/Citator Agent
- Input: the Reranker's output. Output: same list, minus any case flagged as overruled/bad law in Module 1's metadata, with the overruling case substituted in where available.
- **Definition of done:** Given a reranked result set that includes a known-overruled test case, this agent removes it and the output list no longer contains it.

### Module 5 — Structured Reasoning Agent
- Input: validated case list + original query. Output: a strict Pydantic-modeled IRAC structure (Issue, Rule, Application, Conclusion), where every claim in Rule/Application must reference a specific item from the input case list (no free-floating claims).
- **Definition of done:** Output validates against the Pydantic schema every time (test with 10+ queries), and every citation in the output can be traced back to an item in the input list (no citations invented at this stage — that's expected, since verification happens next).
- **Optional future optimization (not required for MVP):** splitting this into a fast/cheap model for simple factual lookups and a stronger reasoning model for complex multi-issue queries, routed by the Query Analyzer Agent's classification. Only pursue this after the core pipeline is working and benchmarked — it's a cost/latency optimization, not a correctness requirement.

### Module 6 — Citation Verifier Agent
- Input: the IRAC output + the original source chunks. Output: each citation marked verified/flagged, with unverifiable claims either stripped or the system abstaining on that specific claim.
- Design this as a reusable component from the start (a function/class that takes a list of citations + source text and returns verification results), not something hardwired to the research pipeline — Module 9.5 (document upload) and Module 8 (drafting) both call this same component.
- This is the most complex agent — budget the most time here.
- **Definition of done:** Run against the LePhantomCite-style test (take a few real citations, deliberately corrupt/swap them, feed through) — the agent must catch the corrupted ones. Log precision/recall on a small hand-built test set.

### Module 7 — Research Pipeline Orchestration
- Wire Modules 1.5–6 together sequentially via PydanticAI, with a shared state object.
- Handle failure states explicitly: sanitization rejects the query → return the rejection reason immediately; zero retrieval results → short-circuit with a clear message; verifier flags everything → return an abstention message, not a broken answer.
- Add the pipeline-trace logging here (which stage did what, what got filtered/reranked/rejected) — this feeds both the "how this was built" UI panel and the ablation study later.
- **Definition of done:** A single function/endpoint takes a raw query string and returns a complete, verified IRAC response plus a trace log, with no manual wiring — this is the object the backend API will call.

### Module 8 — Drafting Agent (Template-Constrained, with Action Gate)
- Set up `document_templates` table (see schema in Section 6) and populate with a small number of real templates (start with 2–3: e.g., a demand letter and one motion type — expand later).
- **Template Matcher:** given a research output + jurisdiction, selects the right template.
- **Section-by-Section Drafter:** generates the document one section at a time (not the whole document in one prompt), pulling IRAC content from Module 7's output into the "argument" section.
- **Citation Injector/Formatter:** standardizes citations into Bluebook format.
- Re-run the drafted output back through Module 6 (Citation Verifier) before returning it — do not skip this step.
- **Action gate:** the drafted document is returned with status `pending_review`, not auto-exportable. The document only becomes exportable after the user explicitly approves it (see Module 9's approval endpoint and Module 12's frontend approve/reject UI). This is a deliberate trust boundary — the system drafts, but never finalizes, a legal document without a human sign-off.
- **Definition of done:** Given a completed research thread, the agent produces a document matching the selected template's required sections, with all citations re-verified, status set to `pending_review`, and no section left as an unfilled placeholder.

### Module 9 — Backend API (FastAPI)
- Auth: Supabase Google OAuth, verify JWT on protected routes.
- Endpoints: `POST /research` (calls Module 7), `POST /draft` (calls Module 8), `POST /draft/{id}/approve` and `POST /draft/{id}/reject` (action gate), `GET /threads`, `POST /threads/{id}/feedback` (thumbs up/down), `GET /threads/{id}/export` (PDF/DOCX via python-docx/WeasyPrint — only permitted when a draft's status is `approved`), credits check/decrement middleware on `/research`, `/draft`, and `/verify-document`.
- **Definition of done:** All endpoints testable via curl/Postman with a real Supabase-authenticated user, correctly enforcing credit balance (rejecting requests at 0 credits), correctly blocking export of an unapproved draft, and returning real pipeline output, not mocked data.

### Module 9.5 — Document Upload & Citation Audit
- Pipeline: user document (PDF/DOCX/TXT) → Module 1.5 (sanitization, applied to extracted text too) → text extraction → citation extraction → parallel verification → audit report.
- **Extraction guardrails:** use pdfplumber/pypdf for PDF, python-docx for DOCX, plain read for TXT. Enforce a page/size limit (e.g., 20 pages). If extracted character count is below a sane threshold for the document's length (e.g., under 50 characters on a 5-page PDF), fail fast with a clear "scanned/image-only document detected, please upload a searchable, text-based file" error rather than attempting OCR.
- **Citation extraction:** combine regex patterns for standard Bluebook citation formats (e.g., U.S. Reports, Federal Reporter, Supreme Court Reporter patterns) with a fast LLM pass to catch citations regex misses or that are written irregularly.
- **Verification:** feed extracted citations through Module 6's Citation Verifier component (reused, not reimplemented) and through Module 4's validity check for overruled/bad law status. Run citations in parallel batches, not sequentially, so a multi-page document doesn't process one citation at a time.
- Output: a structured audit report — each citation with status (clean / flagged / overruled) — in the same shape as the verification badge used elsewhere in the product, so the frontend can reuse existing UI components.
- **Definition of done:** Given a test document with a mix of real, corrupted, and overruled citations, the endpoint returns a correct per-citation status report, processing a ~15-page document in well under a minute.

### Module 10 — Frontend: Auth + Shell
- Landing page (static content + a stats section — even if stats are pulled from a simple aggregate query initially, e.g., "X citations verified, Y% flagged").
- Login page with Google sign-in via Supabase client SDK.
- App shell/navigation: Research, Draft, Document Audit, Pricing.
- **Definition of done:** A user can land on the homepage, sign in with Google, and reach an empty authenticated app shell.

### Module 11 — Frontend: Research/Chat Page
- Chat interface calling `POST /research`.
- Trace panel (collapsible, shows stage-by-stage progress from the trace log, including sanitization/reranking steps).
- Verification badge per answer.
- Source inspector panel (click citation → see source snippet, fetched via a small backend endpoint if not already included in the response).
- Thumbs up/down buttons.
- Saved threads list (sidebar), calling `GET /threads`.
- **Definition of done:** A logged-in user can ask a question, see a real verified answer with trace + badge + source inspection working, and see it appear in their thread history on reload.

### Module 12 — Frontend: Drafting Page
- Select a saved research thread → select a template → view generated draft section-by-section → explicit **Approve** / **Reject** buttons (action gate) → export button only enabled once approved.
- **Definition of done:** A user can go from an existing research thread to a downloaded DOCX/PDF draft, end to end, and cannot export without first approving.

### Module 13 — Frontend: Document Audit Page
- Upload widget (PDF/DOCX/TXT only, with clear file-type/size restrictions shown in the UI).
- Audit report view reusing the verification badge/trust-indicator components from Module 11 (green/yellow/red per citation).
- **Definition of done:** A user can upload a test document and see a correct, readable audit report end to end, using the same visual components as the research page.

### Module 14 — Pricing / Credits
- Pricing page (static content + Stripe test-mode checkout is enough — doesn't need to be production billing for a demo).
- Credit balance display in the app shell.
- Backend enforcement already done in Module 9 — this module is mostly frontend + wiring Stripe test mode to top up credits.
- **Definition of done:** A test user can see their credit balance drop after a research/draft/document-audit call, and top it up via a Stripe test-mode transaction.

### Module 15 — Deployment
- Backend: Dockerize, deploy to Render or Fly.io.
- Frontend: deploy to Vercel.
- Confirm Supabase auth redirect URLs, CORS, and env vars are correctly set for the deployed URLs (not just localhost).
- **Definition of done:** The full flow (sign in → research → draft → approve → document audit → export → credits) works on the live hosted URL, not just locally.

### Module 16 — Evaluation & Benchmarking (for the research paper — can run in parallel with Modules 10–15 once Module 7 is done)
- Run the Dahl et al. (2024) benchmark question set (or a representative sample, given ~200K questions) through Module 7, log hallucination rate.
- Ablation study: disable Module 6 (Verifier), then also Module 4 (Validity/Citator), then also Module 3.5 (Reranker), re-run a subset, measure hallucination rate at each step.
- Run the LePhantomCite-style injected-citation test specifically against Module 6, report precision/recall/F1.
- Compile results against the published baselines (Dahl et al.: 58–88%; Magesh et al.: 17–43%).
- **Definition of done:** A results table showing baseline vs. full pipeline vs. each ablation step, ready to drop into Paper 2.

---

## 5. Demo Flow (for both project review and YC pitch)

Structure the live demo around a single scenario that shows the system's differentiation, not just its features:

1. **The loaded query:** Ask a question that hinges on an overturned precedent or a false legal premise.
2. **The live trace:** Open the trace panel and show sanitization passing, retrieval and reranking narrowing to the right passages, and the Validity/Citator Agent catching the overruled case, dropping it, and substituting the current governing standard.
3. **The draft:** Generate a short motion section in IRAC style with verified pincites, show the action-gate approval step, then export.
4. **The audit:** Upload an external document with a deliberately injected bad citation and show the audit report flagging it immediately.

This sequence is deliberately built to only use features already in the module list above — nothing demo-specific needs to be built separately.

---

## 6. Database Schema (Postgres / Supabase)

```sql
-- Case law metadata (populated in Module 1)
CREATE TABLE cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    citation TEXT NOT NULL,
    jurisdiction VARCHAR(100),
    decided_date DATE,
    still_good_law BOOLEAN DEFAULT TRUE,
    overruled_by UUID REFERENCES cases(id),
    source_url TEXT
);

-- Citation graph (populated in Module 1)
CREATE TABLE case_citations (
    citing_case_id UUID REFERENCES cases(id),
    cited_case_id UUID REFERENCES cases(id),
    PRIMARY KEY (citing_case_id, cited_case_id)
);

-- Document templates (populated in Module 8)
CREATE TABLE document_templates (
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
    user_id UUID NOT NULL, -- references Supabase auth.users
    query TEXT NOT NULL,
    result_json JSONB NOT NULL, -- IRAC output + verification metadata
    trace_json JSONB NOT NULL,  -- stage-by-stage trace log
    feedback SMALLINT, -- -1, 0, 1 for thumbs down/none/up
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Drafted documents (Module 8/12)
CREATE TABLE legal_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID REFERENCES research_threads(id),
    template_id UUID REFERENCES document_templates(id),
    content_json JSONB NOT NULL,
    verification_status VARCHAR(50) DEFAULT 'unverified',
    approval_status VARCHAR(50) DEFAULT 'pending_review', -- pending_review, approved, rejected
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
    user_id UUID PRIMARY KEY, -- references Supabase auth.users
    balance INTEGER NOT NULL DEFAULT 10,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 7. Code Standards (apply to every module, not just at the end)

- **Modularity:** each agent/stage (Modules 1.5–6, 8) must be a self-contained unit with a clear input/output contract (typed via Pydantic models), callable independently of the orchestration layer. No stage should directly import or depend on another stage's internals — they communicate only through the shared state object / defined interfaces.
- **Reusability over duplication:** shared logic (sanitization, citation verification, text extraction, PDF/DOCX export) is written once as a service/utility and imported wherever needed. Module 9.5 and Module 8 must both call Module 6's verifier component rather than each having their own copy; Module 9.5 must reuse Module 1.5's sanitization rather than reimplementing it.
- **Scalability for future features:** design the pipeline orchestration (Module 7) so a new stage can be inserted into the sequence, or an existing one swapped out or disabled (as the ablation study and the reranker's fallback mode both require), without rewriting the other stages. Keep the stage interface consistent (same input/output pattern) across the pipeline for this reason.
- **Separation of concerns:** keep API route handlers thin — they should validate input, call into a service/pipeline function, and format the response. Business logic belongs in service modules, not in route handlers.
- **Configuration over hardcoding:** API keys, model names, credit costs, file-size limits, reranker top-k, and similar values belong in config/environment variables, not hardcoded inline.
- **Naming and structure:** consistent, descriptive naming across backend and frontend; one clear folder per module/domain (e.g., `agents/`, `retrieval/`, `drafting/`, `documents/`, `api/`, `db/`) rather than a flat file dump.
- **Comments:** keep comments minimal — only where the reasoning behind a non-obvious decision genuinely needs explaining. Code should be readable primarily through clear naming and structure, not through comment density.
- **No emojis** anywhere in code, commit messages, log output, or code comments.
- **Error handling:** every external call (LLM, database, file parsing) should fail explicitly and predictably (clear exceptions/error responses), not silently swallow errors.
- **Testing discipline:** each module's "definition of done" test should be kept as an actual runnable test (even a simple script), not just a manual one-off check, so regressions in later modules are caught early.

---

## 8. Base Papers & Datasets (for Module 16, and for context throughout)

- **Dahl, Magesh, Suzgun & Ho (2024)**, "Large Legal Fictions: Profiling Legal Hallucinations in Large Language Models," *Journal of Legal Analysis* 16(1):64–93. Dataset: HuggingFace `reglab/legal_hallucinations`.
- **Magesh, Surani, Dahl, Suzgun, Manning & Ho (2025)**, "Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools," *Journal of Empirical Legal Studies* 22:216–242.
- **Liu, Stammbach & Henderson (2026)**, "Who Checks the Citations? Benchmarking Legal Hallucination Detection" (LePhantomCite), arXiv:2606.21155 — methodology source for Module 6's evaluation.
- **Corpus:** Caselaw Access Project (CAP) / CourtListener.

---

## 9. Future Scope — MCP Tool Integration (not part of this build)

Deferred deliberately — revisit only if time permits after Modules 0–16 are complete and stable. Candidate directions to consider later, not commitments:

- Live case-law/court-status lookups via an MCP server, so the Query Analyzer Agent (acting as an MCP client) could route certain queries to a live tool rather than only the static retrieval corpus.
- Jurisdiction/statute knowledge-base tools exposed via MCP for more current statutory information than a static corpus snapshot.
- A filing-deadline/court-calendar tool for the drafting side of the product.

Do not begin implementing any of this until the core pipeline (Modules 0–16) is working and benchmarked — it's explicitly a post-MVP direction.

---

## 10. YC Positioning (for context, not for the coding bot to implement)

- **Target customer:** solo practitioners, boutique litigation firms (2–20 lawyers), and public defender organizations — not free legal aid for the general public (limited willingness-to-pay, and unauthorized-practice-of-law liability concerns).
- **Core value proposition:** commercial legal-AI tools still hallucinate citations 17–33% of the time; this product is positioned as the first benchmark-backed legal research and drafting system with an independently verified, published-baseline-beating accuracy rate.
- **Differentiation:** a deterministic citation-verification and good-law-filtering layer, not just a bigger or better-prompted model — paired with a public, reproducible benchmark comparison, which most competitors don't offer.

---

## 11. Notes for Whoever Implements This

- Build and verify each module in isolation before wiring it into the next. Do not let the frontend work (Modules 10–14) start until Module 7 (the orchestrated research pipeline) is fully working — there's nothing real to build a UI against otherwise.
- Module 6 (Citation Verifier) is the hardest and most important piece, both for the product's core claim and for the paper, and for Module 9.5. Don't shortcut it to hit a deadline elsewhere.
- The Reranker (Module 3.5) and the fast/reasoning model split (Module 5 note) are both worth having but neither blocks the core benchmark — treat them as "add if time permits," in that priority order.
- The action gate (Module 8/9/12) is a product trust decision, not just a UI nicety — no drafted document should ever be exportable without an explicit user approval step.
- Keep the credits/pricing system in test mode throughout — it exists to make the demo/pitch look like a real product, not to process real payments.
- Everything the frontend needs to "look alive" (trace panel, verification badges, stats) should be built from data the backend is already producing for the paper — don't build separate mock/demo-only data paths.
- Apply Section 7's code standards from Module 0 onward — retrofitting clean structure after the fact costs more than building it in from the start.
