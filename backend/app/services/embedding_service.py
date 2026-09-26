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

# Per BAAI's official bge-base-en-v1.5 usage guidance for asymmetric
# retrieval: prefix the query only, never the ingested chunk text.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model_name)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Return L2-normalized embeddings, one row per input text."""
    return _get_model().encode(texts, normalize_embeddings=True)


def embed_query(query: str) -> np.ndarray:
    """
    Embed a single query for retrieval against case_chunks, applying the
    BGE query-side instruction prefix. Use this (not embed_texts) for any
    search against match_case_chunks/keyword_search_case_chunks.
    """
    return embed_texts([f"{BGE_QUERY_PREFIX}{query}"])[0]
