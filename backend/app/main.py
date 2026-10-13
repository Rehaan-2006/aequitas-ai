from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import research, draft, threads, templates
from app.core.config import settings
from app.services import embedding_service, reranker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Force both torch-backed singletons (embedding model, reranker
    # cross-encoder) to load now, on the main thread, instead of lazily
    # on the first request inside a uvicorn worker thread. See
    # docs/DECISIONS.md for why this was added.
    embedding_service.preload()
    reranker.preload()
    yield


app = FastAPI(title="Aequitas AI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(research.router)
app.include_router(draft.router)
app.include_router(threads.router)
app.include_router(templates.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
