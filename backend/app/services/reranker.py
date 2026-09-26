"""
Module 3.5 -- Reranker.

Cross-encoder reranker that narrows Module 3's wide retrieval (top-20)
to the final top-k most relevant passages (default top-5). No LLM call,
no Openrouter cost — local model via sentence-transformers.

Lazy-loads the CrossEncoder once per process and batches all candidates
into a single predict() call for efficiency.
"""

from functools import lru_cache

from sentence_transformers import CrossEncoder

from app.core.config import settings
from app.services.retrieval import CaseChunk


@lru_cache(maxsize=1)
def _get_reranker_model() -> CrossEncoder:
    return CrossEncoder(settings.reranker_model_name)


def rerank(
    query: str,
    candidates: list[CaseChunk],
    top_n: int | None = None,
) -> list[CaseChunk]:
    """
    Rerank candidates by relevance to the query using a cross-encoder model.

    Takes the top_n most-relevant candidates after reranking, sorted by
    rerank_score descending. If top_n is None, defaults to the config
    setting. Preserves existing score, dense_similarity, and sparse_rank
    fields; adds rerank_score to each result.

    Args:
        query: the original search query
        candidates: list of CaseChunk candidates to rerank (typically top-20 from retrieval)
        top_n: number of top results to return (None = use config default)

    Returns:
        list of top-n CaseChunk objects, sorted by rerank_score descending
    """
    if not candidates:
        return []

    if top_n is None:
        top_n = settings.reranker_top_n

    model = _get_reranker_model()

    # Batch score all candidates in one predict() call using (query, chunk_text) pairs
    candidate_texts = [chunk.chunk_text for chunk in candidates]
    query_chunk_pairs = [[query, text] for text in candidate_texts]
    scores = model.predict(query_chunk_pairs)

    # Assign rerank_score to each candidate and sort descending
    for chunk, score in zip(candidates, scores):
        chunk.rerank_score = float(score)

    reranked = sorted(candidates, key=lambda c: c.rerank_score or 0.0, reverse=True)
    return reranked[:top_n]
