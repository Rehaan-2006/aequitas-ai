# Aequitas AI

A legal research and drafting assistant that independently verifies every
citation it shows a user, and abstains rather than guesses when it can't
confirm something. Benchmarked against published hallucination-rate baselines
for general-purpose LLMs and commercial legal-AI tools.

Full build plan: [`docs/build-plan.md`](docs/build-plan.md). Read it before
starting any module — it defines build order, tech stack, and a hard
"definition of done" gate per module.

**Status: Module 0 (Repo & Environment Setup) — done.**

---

## Quick start

```
git clone <repo-url>
cd aequitas-ai

# Backend
cd backend
cp .env.example .env      # ask a teammate for real values once they're needed
cd ..

# Everything, via Docker
docker compose up
```

- Backend: http://localhost:8000/health → `{"status": "ok"}`
- Frontend: http://localhost:5173 → blank page (nothing built yet, by design — see Module 0's definition of done in the build plan)

Or run each side natively without Docker — see `backend/README.md` and
`frontend/README.md`.

---

## What's in here

```
backend/    FastAPI, managed with uv. See backend/README.md.
frontend/   React (Vite) + Tailwind CSS. See frontend/README.md.
docs/       Build plan and (later) architecture notes.
```

## Stack

| Layer            | Choice                                  |
| ---------------- | ---------------------------------------- |
| Backend          | FastAPI (Python), managed with `uv`      |
| Agents           | PydanticAI (from Module 4 onward)        |
| Frontend         | React (Vite) + Tailwind CSS              |
| Database/Auth    | Supabase (Postgres + Auth + Storage)     |
| Vector search     | pgvector (from Module 1 onward)          |
| Hosting (planned) | Backend on Render/Fly.io, frontend on Vercel |

Full rationale for each choice is in the build plan.

## Contributing

Each module has its own definition-of-done checklist in the build plan —
treat it as a hard gate before starting the next module. Apply the code
standards in the build plan's Section 7 from the start, not retroactively.
