"""
Shared local embedding model access.

Loads BAAI/bge-base-en-v1.5 once per process and exposes a plain
encode function. Any service that needs semantic-similarity checks
(sanitization, retrieval) should call this instead of instantiating
its own SentenceTransformer.
"""

from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import settings


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model_name)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Return L2-normalized embeddings, one row per input text."""
    return _get_model().encode(texts, normalize_embeddings=True)
