"""
Module 4 -- Validity/Citator Agent tests.

Mocked suite (default CI, no live credentials): a fake Supabase client
stands in for the batched `cases`/`case_chunks` table lookups so
passthrough, drop, and substitution logic is exercised deterministically.
Filter params (`select`/`eq`/`in_`) are accepted but ignored, same
convention as tests/test_retrieval_agent.py's FakeSupabaseClient --
canned table data is scoped per test rather than the fake re-implementing
real filtering.

Integration tests at the bottom require live Supabase credentials and
are excluded from default CI (`pytest -m "not integration"`). They only
exercise the passthrough/drop paths, since overruled_by is NULL for all
502 rows in the current corpus -- see docs/DECISIONS.md.
"""

import pytest

from app.core.config import settings
from app.services.retrieval import CaseChunk
from app.services.validity_checker import check_validity


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def execute(self):
        return _FakeResult(self._data)


class FakeSupabaseClient:
    """Stands in for a real Supabase client: table() calls are resolved
    by name against canned response data, ignoring filter params."""

    def __init__(self, table_data: dict | None = None):
        self._table_data = table_data or {}

    def table(self, name):
        return _FakeQuery(self._table_data.get(name, []))


def _install_fake_client(monkeypatch, table_data=None):
    client = FakeSupabaseClient(table_data=table_data)
    monkeypatch.setattr("app.services.validity_checker.get_supabase_client", lambda: client)
    return client


def _make_chunk(case_id: str, case_name: str = "Test Case", citation: str = "Test Citation") -> CaseChunk:
    return CaseChunk(
        id=f"chunk-{case_id}",
        case_id=case_id,
        chunk_index=0,
        chunk_text=f"Chunk text for {case_name}",
        case_name=case_name,
        citation=citation,
    )


# =====================
# Mocked tests (default CI)
# =====================


def test_all_valid_passthrough(monkeypatch):
    candidates = [
        _make_chunk("case-a", "Case A"),
        _make_chunk("case-b", "Case B"),
    ]
    table_data = {
        "cases": [
            {"id": "case-a", "case_name": "Case A", "is_overruled": False, "overruled_by": None},
            {"id": "case-b", "case_name": "Case B", "is_overruled": False, "overruled_by": None},
        ],
        "case_chunks": [],
    }
    _install_fake_client(monkeypatch, table_data=table_data)

    result = check_validity(candidates)

    assert [chunk.id for chunk in result.valid_chunks] == ["chunk-case-a", "chunk-case-b"]
    assert result.dropped == []
    assert result.substitutions == []


def test_drop_with_no_substitute(monkeypatch):
    candidates = [
        _make_chunk("case-a", "Case A"),
        _make_chunk("case-bad", "Overruled Case"),
    ]
    table_data = {
        "cases": [
            {"id": "case-a", "case_name": "Case A", "is_overruled": False, "overruled_by": None},
            {"id": "case-bad", "case_name": "Overruled Case", "is_overruled": True, "overruled_by": None},
        ],
        "case_chunks": [],
    }
    _install_fake_client(monkeypatch, table_data=table_data)

    result = check_validity(candidates)

    assert [chunk.id for chunk in result.valid_chunks] == ["chunk-case-a"]
    assert len(result.dropped) == 1
    assert result.dropped[0].case_id == "case-bad"
    assert result.dropped[0].case_name == "Overruled Case"
    assert "no replacement" in result.dropped[0].reason
    assert result.substitutions == []


def test_substitute_with_replacement_chunk(monkeypatch):
    candidates = [_make_chunk("case-bad", "Overruled Case")]
    table_data = {
        "cases": [
            {"id": "case-bad", "case_name": "Overruled Case", "is_overruled": True, "overruled_by": "case-good"},
            {
                "id": "case-good",
                "case_name": "Governing Case",
                "citation": "Governing Citation",
                "court": "Ninth Circuit",
                "jurisdiction": "Federal",
                "decision_date": "2005-01-01",
            },
        ],
        "case_chunks": [
            {
                "id": "chunk-good-0",
                "case_id": "case-good",
                "chunk_index": 0,
                "chunk_text": "Representative excerpt of the governing case.",
            },
        ],
    }
    _install_fake_client(monkeypatch, table_data=table_data)

    result = check_validity(candidates)

    assert len(result.valid_chunks) == 1
    substitute = result.valid_chunks[0]
    assert substitute.id == "chunk-good-0"
    assert substitute.case_id == "case-good"
    assert substitute.case_name == "Governing Case"
    assert substitute.citation == "Governing Citation"

    assert result.dropped == []
    assert len(result.substitutions) == 1
    sub = result.substitutions[0]
    assert sub.original_case_id == "case-bad"
    assert sub.original_case_name == "Overruled Case"
    assert sub.replacement_case_id == "case-good"
    assert sub.replacement_case_name == "Governing Case"


def test_mixed_batch_passthrough_drop_and_substitute(monkeypatch):
    candidates = [
        _make_chunk("case-a", "Valid Case"),
        _make_chunk("case-bad", "Overruled No Replacement"),
        _make_chunk("case-old", "Overruled With Replacement"),
    ]
    table_data = {
        "cases": [
            {"id": "case-a", "case_name": "Valid Case", "is_overruled": False, "overruled_by": None},
            {"id": "case-bad", "case_name": "Overruled No Replacement", "is_overruled": True, "overruled_by": None},
            {"id": "case-old", "case_name": "Overruled With Replacement", "is_overruled": True, "overruled_by": "case-new"},
            {
                "id": "case-new",
                "case_name": "New Governing Case",
                "citation": "New Citation",
                "court": "Fifth Circuit",
                "jurisdiction": "Federal",
                "decision_date": "2010-01-01",
            },
        ],
        "case_chunks": [
            {
                "id": "chunk-new-0",
                "case_id": "case-new",
                "chunk_index": 0,
                "chunk_text": "Representative excerpt of the new governing case.",
            },
        ],
    }
    _install_fake_client(monkeypatch, table_data=table_data)

    result = check_validity(candidates)

    valid_ids = [chunk.id for chunk in result.valid_chunks]
    assert valid_ids == ["chunk-case-a", "chunk-new-0"]

    assert len(result.dropped) == 1
    assert result.dropped[0].case_id == "case-bad"

    assert len(result.substitutions) == 1
    assert result.substitutions[0].original_case_id == "case-old"
    assert result.substitutions[0].replacement_case_id == "case-new"


def test_empty_candidates_returns_empty(monkeypatch):
    result = check_validity([])
    assert result.valid_chunks == []
    assert result.dropped == []
    assert result.substitutions == []


def test_replacement_missing_representative_chunk_falls_back_to_drop(monkeypatch):
    """If overruled_by points at a case with no chunk_index=0 row, drop
    the original candidate instead of substituting nothing."""
    candidates = [_make_chunk("case-bad", "Overruled Case")]
    table_data = {
        "cases": [
            {"id": "case-bad", "case_name": "Overruled Case", "is_overruled": True, "overruled_by": "case-ghost"},
            {"id": "case-ghost", "case_name": "Ghost Case", "citation": "Ghost Citation"},
        ],
        "case_chunks": [],
    }
    _install_fake_client(monkeypatch, table_data=table_data)

    result = check_validity(candidates)

    assert result.valid_chunks == []
    assert len(result.dropped) == 1
    assert result.dropped[0].case_id == "case-bad"
    assert "replacement case has no representative chunk" in result.dropped[0].reason
    assert result.substitutions == []


# =====================
# Integration (live Supabase, 502-case corpus)
# =====================


@pytest.mark.integration
class TestValidityCheckerIntegration:
    def _check_credentials(self):
        assert (
            settings.supabase_url and settings.supabase_service_role_key
        ), "Missing SUPABASE environment variables"

    def test_live_validity_check_passthrough_and_drop(self):
        """
        Confirms the batched validity lookup works against the real
        502-case corpus. overruled_by is NULL for every row currently
        (see docs/DECISIONS.md), so this only exercises the passthrough
        and drop paths, not substitution -- expected, not a gap.
        """
        self._check_credentials()
        from app.db.supabase_client import get_supabase_client

        client = get_supabase_client()
        overruled_rows = (
            client.table("cases").select("id, case_name").eq("is_overruled", True).limit(2).execute().data
        )
        valid_rows = (
            client.table("cases").select("id, case_name").eq("is_overruled", False).limit(2).execute().data
        )

        assert overruled_rows, "No is_overruled=True rows found -- did the synthetic flag patch get applied?"
        assert valid_rows, "No is_overruled=False rows found"

        candidates = [
            CaseChunk(
                id=f"chunk-{row['id']}",
                case_id=row["id"],
                chunk_index=0,
                chunk_text="placeholder",
                case_name=row["case_name"],
                citation="placeholder citation",
            )
            for row in overruled_rows + valid_rows
        ]

        result = check_validity(candidates)

        dropped_ids = {d.case_id for d in result.dropped}
        valid_ids = {chunk.case_id for chunk in result.valid_chunks}

        for row in overruled_rows:
            assert row["id"] in dropped_ids
        for row in valid_rows:
            assert row["id"] in valid_ids

        assert result.substitutions == []

        print(f"\nDropped {len(result.dropped)} overruled case(s), passed through {len(result.valid_chunks)}.")
