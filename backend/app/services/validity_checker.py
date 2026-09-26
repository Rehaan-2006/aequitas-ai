"""
Module 4 -- Validity/Citator Agent.

Deterministic DB lookup, no LLM call (same category as retrieval.py and
reranker.py): given the Reranker's narrowed candidate list, checks each
candidate's case against is_overruled/overruled_by and either passes it
through unchanged, drops it, or substitutes the governing replacement
case's representative chunk in its place.

overruled_by is NULL for all 502 rows in the current corpus (is_overruled
itself is synthetic, ~10% hash-flagged for dev purposes -- see
docs/DECISIONS.md, "is_overruled hardcoded false" entry), so the
substitution branch below cannot be exercised against live data yet --
it's covered by mocked tests only until that column is populated.

Does not request more candidates from retrieval if valid_chunks ends up
smaller than the input list -- that's a pipeline-orchestration decision
for Module 7, which doesn't exist yet. This module only reports what it
did; something else decides whether the result set is too thin.
"""

from pydantic import BaseModel

from app.db.supabase_client import get_supabase_client
from app.services.retrieval import CaseChunk


class DroppedCase(BaseModel):
    case_id: str
    case_name: str
    reason: str


class Substitution(BaseModel):
    original_case_id: str
    original_case_name: str
    replacement_case_id: str
    replacement_case_name: str


class ValidityCheckResult(BaseModel):
    valid_chunks: list[CaseChunk]
    dropped: list[DroppedCase]
    substitutions: list[Substitution]


def _fetch_case_status(client, case_ids: list[str]) -> dict[str, dict]:
    if not case_ids:
        return {}
    response = (
        client.table("cases")
        .select("id, case_name, is_overruled, overruled_by")
        .in_("id", case_ids)
        .execute()
    )
    return {row["id"]: row for row in (response.data or [])}


def _fetch_replacement_chunks(client, replacement_case_ids: list[str]) -> dict[str, CaseChunk]:
    """
    Fetches each replacement case's chunk_index=0 chunk directly from
    case_chunks -- no fresh vector search -- as its representative
    excerpt. Batched across every replacement case needed in this call,
    not queried per-candidate. Case metadata (name/citation/court/
    jurisdiction/date) is fetched in a second batched query and merged
    in, since case_chunks alone doesn't carry it.

    A replacement case_id with no chunk_index=0 row (unexpected, but not
    impossible) is simply absent from the returned dict; the caller
    falls back to dropping the original candidate in that case.
    """
    if not replacement_case_ids:
        return {}

    case_rows = (
        client.table("cases")
        .select("id, case_name, citation, court, jurisdiction, decision_date")
        .in_("id", replacement_case_ids)
        .execute()
    )
    cases_by_id = {row["id"]: row for row in (case_rows.data or [])}

    chunk_rows = (
        client.table("case_chunks")
        .select("id, case_id, chunk_index, chunk_text")
        .eq("chunk_index", 0)
        .in_("case_id", replacement_case_ids)
        .execute()
    )

    replacements: dict[str, CaseChunk] = {}
    for row in chunk_rows.data or []:
        case_meta = cases_by_id.get(row["case_id"])
        if case_meta is None:
            continue
        replacements[row["case_id"]] = CaseChunk(
            id=row["id"],
            case_id=row["case_id"],
            chunk_index=row["chunk_index"],
            chunk_text=row["chunk_text"],
            case_name=case_meta["case_name"],
            citation=case_meta["citation"],
            court=case_meta.get("court"),
            jurisdiction=case_meta.get("jurisdiction"),
            decision_date=case_meta.get("decision_date"),
        )
    return replacements


def check_validity(candidates: list[CaseChunk]) -> ValidityCheckResult:
    """
    Single entry point for the Validity/Citator Agent.

    A candidate whose case has no status row in `cases` (not expected in
    practice, since candidates always originate from data already in the
    corpus) is passed through unchanged rather than dropped -- there's no
    evidence it's overruled.
    """
    if not candidates:
        return ValidityCheckResult(valid_chunks=[], dropped=[], substitutions=[])

    client = get_supabase_client()
    case_ids = list({chunk.case_id for chunk in candidates})
    status_by_case_id = _fetch_case_status(client, case_ids)

    replacement_case_ids = list(
        {
            status["overruled_by"]
            for status in status_by_case_id.values()
            if status.get("is_overruled") and status.get("overruled_by")
        }
    )
    replacements_by_case_id = _fetch_replacement_chunks(client, replacement_case_ids)

    valid_chunks: list[CaseChunk] = []
    dropped: list[DroppedCase] = []
    substitutions: list[Substitution] = []

    for chunk in candidates:
        status = status_by_case_id.get(chunk.case_id)

        if status is None or not status.get("is_overruled"):
            valid_chunks.append(chunk)
            continue

        overruled_by = status.get("overruled_by")
        replacement = replacements_by_case_id.get(overruled_by) if overruled_by else None

        if replacement is None:
            reason = (
                "overruled with no replacement case available"
                if not overruled_by
                else "overruled; replacement case has no representative chunk"
            )
            dropped.append(DroppedCase(case_id=chunk.case_id, case_name=chunk.case_name, reason=reason))
            continue

        valid_chunks.append(replacement)
        substitutions.append(
            Substitution(
                original_case_id=chunk.case_id,
                original_case_name=chunk.case_name,
                replacement_case_id=replacement.case_id,
                replacement_case_name=replacement.case_name,
            )
        )

    return ValidityCheckResult(valid_chunks=valid_chunks, dropped=dropped, substitutions=substitutions)
