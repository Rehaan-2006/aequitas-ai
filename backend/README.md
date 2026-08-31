# Aequitas AI — Backend

FastAPI, managed with `uv`.

## Local dev

```
uv sync
cp .env.example .env      # fill in real values as they're needed by later modules
uv run uvicorn app.main:app --reload
```

Runs at http://localhost:8000. Health check: `GET /health`.

## Tests

```
uv run pytest
```

## Structure

```
app/
  main.py     FastAPI app + routes (kept thin per Section 7 of the build plan)
  core/       config, settings
  api/        route modules (populated from Module 1 onward)
  db/         database access (populated from Module 1 onward)
tests/
```
