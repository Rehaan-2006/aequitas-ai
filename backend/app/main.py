from fastapi import FastAPI

from app.api import research, draft, threads

app = FastAPI(title="Aequitas AI Backend")

# Include API routers
app.include_router(research.router)
app.include_router(draft.router)
app.include_router(threads.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
