"""
Module 3 -- Hybrid Retrieval Agent.

Combines dense (semantic) and sparse (keyword) search over case_chunks,
fused via Reciprocal Rank Fusion (RRF), with a citation-graph score boost
and structured jurisdiction/date filters. Purely deterministic -- no LLM
call in this module. Intentionally over-retrieves (wide top-k); narrowing
to the final few passages is the separate, not-yet-built Reranker
(Module 3.5).

NL-based filter extraction (parsing jurisdiction/date out of the query
string itself) is deferred to a future module -- jurisdiction/date_from/
date_to here are explicit, structured parameters the caller must supply.
"""

from datetime import date

from pydantic import BaseModel

from app.core.config import settings
from app.db.supabase_client import get_supabase_client
from app.services.embedding_service import embed_query


class CaseChunk(BaseModel):
    id: str
    case_id: str
    chunk_index: int
    chunk_text: str
    case_name: str
    citation: str
    court: str | None = None
    jurisdiction: str | None = None
    decision_date: str | None = None
    dense_similarity: float | None = None
    sparse_rank: float | None = None
    score: float = 0.0


def _row_to_chunk(row: dict) -> CaseChunk:
    return CaseChunk(
        id=row["id"],
        case_id=row["case_id"],
        chunk_index=row["chunk_index"],
        chunk_text=row["chunk_text"],
        case_name=row["case_name"],
        citation=row["citation"],
        court=row.get("court"),
        jurisdiction=row.get("jurisdiction"),
        decision_date=row.get("decision_date"),
    )


def _passes_filters(
    row: dict,
    jurisdiction: str | None,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    if jurisdiction is not None and row.get("jurisdiction") != jurisdiction:
        return False

    if date_from or date_to:
        raw_date = row.get("decision_date")
        if not raw_date:
            return False
        row_date = date.fromisoformat(raw_date)
        if date_from and row_date < date.fromisoformat(date_from):
            return False
        if date_to and row_date > date.fromisoformat(date_to):
            return False

    return True


def _fetch_jurisdictions(client, case_ids: list[str]) -> dict[str, str | None]:
    if not case_ids:
        return {}
    response = client.table("cases").select("id, jurisdiction").in_("id", case_ids).execute()
    return {row["id"]: row.get("jurisdiction") for row in (response.data or [])}


def _dense_search(client, query: str, candidate_count: int) -> list[dict]:
    """
    Reuses Module 1's match_case_chunks RPC as-is. That RPC doesn't return
    jurisdiction (it predates this module's filter requirement), so it's
    merged in from a single batched `cases` lookup rather than modifying
    the existing, already-verified RPC signature.
    """
    query_embedding = embed_query(query).tolist()
    response = client.rpc(
        "match_case_chunks",
        {
            "query_embedding": query_embedding,
            "match_threshold": settings.retrieval_match_threshold,
            "match_count": candidate_count,
        },
    ).execute()
    rows = response.data or []

    case_ids = list({row["case_id"] for row in rows})
    jurisdictions = _fetch_jurisdictions(client, case_ids)
    for row in rows:
        row["jurisdiction"] = jurisdictions.get(row["case_id"])

    return rows


def _sparse_search(client, query: str, candidate_count: int) -> list[dict]:
    response = client.rpc(
        "keyword_search_case_chunks",
        {"query_text": query, "match_count": candidate_count},
    ).execute()
    return response.data or []


def _reciprocal_rank_fusion(
    dense_rows: list[dict], sparse_rows: list[dict], k: int
) -> dict[str, CaseChunk]:
    fused: dict[str, CaseChunk] = {}

    for rank, row in enumerate(dense_rows, start=1):
        chunk = fused.setdefault(row["id"], _row_to_chunk(row))
        chunk.dense_similarity = row.get("similarity")
        chunk.score += 1.0 / (k + rank)

    for rank, row in enumerate(sparse_rows, start=1):
        chunk = fused.setdefault(row["id"], _row_to_chunk(row))
        chunk.sparse_rank = row.get("rank")
        chunk.score += 1.0 / (k + rank)

    return fused


def _apply_citation_boost(client, chunks: dict[str, CaseChunk]) -> None:
    """
    Boosts candidates connected by a direct citation edge to another
    candidate already in the result set. Deliberately not a graph
    traversal -- only direct edges between candidates present in `chunks`
    are considered.
    """
    case_ids = list({chunk.case_id for chunk in chunks.values()})
    if len(case_ids) < 2:
        return

    response = (
        client.table("case_citations")
        .select("citing_case_id, cited_case_id")
        .in_("citing_case_id", case_ids)
        .in_("cited_case_id", case_ids)
        .execute()
    )

    connected_case_ids: set[str] = set()
    for edge in response.data or []:
        citing_id = edge.get("citing_case_id")
        cited_id = edge.get("cited_case_id")
        if citing_id is not None and cited_id is not None:
            connected_case_ids.add(citing_id)
            connected_case_ids.add(cited_id)

    for chunk in chunks.values():
        if chunk.case_id in connected_case_ids:
            chunk.score += settings.retrieval_citation_boost


def retrieve(
    query: str,
    jurisdiction: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[CaseChunk]:
    """
    Single entry point for the Hybrid Retrieval Agent.

    Returns up to `settings.hybrid_retrieval_top_k` CaseChunks, fused from
    dense + sparse search and boosted by citation-graph connectivity,
    ordered by descending fused score.
    """
    client = get_supabase_client()
    candidate_pool = settings.hybrid_retrieval_candidate_pool

    dense_rows = _dense_search(client, query, candidate_pool)
    sparse_rows = _sparse_search(client, query, candidate_pool)

    dense_rows = [row for row in dense_rows if _passes_filters(row, jurisdiction, date_from, date_to)]
    sparse_rows = [row for row in sparse_rows if _passes_filters(row, jurisdiction, date_from, date_to)]

    fused = _reciprocal_rank_fusion(dense_rows, sparse_rows, settings.retrieval_rrf_k)
    _apply_citation_boost(client, fused)

    ranked = sorted(fused.values(), key=lambda chunk: chunk.score, reverse=True)
    return ranked[: settings.hybrid_retrieval_top_k]
