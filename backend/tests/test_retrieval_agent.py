"""
Module 3 -- Hybrid Retrieval Agent tests.

Mocked suite (default CI, no live credentials): a fake Supabase client
stands in for match_case_chunks / keyword_search_case_chunks / table
lookups so fusion, boosting, and filtering logic is exercised
deterministically. The real local embedding model still loads (already
an accepted CI cost per docs/DECISIONS.md, "Replace keyword/regex scope
and injection checks with embedding similarity") but its output value
never affects these tests, since the fake client's canned responses
don't depend on the query embedding passed in.

Integration tests at the bottom require live Supabase credentials and
are excluded from default CI (`pytest -m "not integration"`).
"""

import pytest

from app.core.config import settings
from app.services.retrieval import retrieve


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def execute(self):
        return _FakeResult(self._data)


class FakeSupabaseClient:
    """Stands in for a real Supabase client: rpc() and table() calls are
    resolved by name against canned response data, ignoring parameters."""

    def __init__(self, rpc_data: dict | None = None, table_data: dict | None = None):
        self._rpc_data = rpc_data or {}
        self._table_data = table_data or {}

    def rpc(self, name, params):
        return _FakeQuery(self._rpc_data.get(name, []))

    def table(self, name):
        return _FakeQuery(self._table_data.get(name, []))


def _install_fake_client(monkeypatch, rpc_data=None, table_data=None):
    client = FakeSupabaseClient(rpc_data=rpc_data, table_data=table_data)
    monkeypatch.setattr("app.services.retrieval.get_supabase_client", lambda: client)
    return client


# =====================
# Dense-only / sparse-only
# =====================


def test_dense_only_results(monkeypatch):
    dense_rows = [
        {
            "id": "chunk-a1", "case_id": "case-a", "chunk_index": 0,
            "chunk_text": "dense hit one", "similarity": 0.9,
            "case_name": "Case A", "citation": "A citation", "court": "Fifth Circuit",
            "decision_date": "2000-01-01",
        },
        {
            "id": "chunk-b1", "case_id": "case-b", "chunk_index": 0,
            "chunk_text": "dense hit two", "similarity": 0.8,
            "case_name": "Case B", "citation": "B citation", "court": "Ninth Circuit",
            "decision_date": "2001-01-01",
        },
    ]
    table_data = {
        "cases": [
            {"id": "case-a", "jurisdiction": "Federal"},
            {"id": "case-b", "jurisdiction": "Federal"},
        ],
        "case_citations": [],
    }
    _install_fake_client(monkeypatch, rpc_data={"match_case_chunks": dense_rows, "keyword_search_case_chunks": []}, table_data=table_data)

    results = retrieve("some query")

    assert [chunk.id for chunk in results] == ["chunk-a1", "chunk-b1"]
    assert results[0].dense_similarity == 0.9
    assert results[0].sparse_rank is None


def test_sparse_only_results(monkeypatch):
    sparse_rows = [
        {
            "id": "chunk-c1", "case_id": "case-c", "chunk_index": 0,
            "chunk_text": "sparse hit one", "rank": 0.9,
            "case_name": "Case C", "citation": "C citation", "court": "Fifth Circuit",
            "jurisdiction": "Federal", "decision_date": "2002-01-01",
        },
        {
            "id": "chunk-d1", "case_id": "case-d", "chunk_index": 0,
            "chunk_text": "sparse hit two", "rank": 0.5,
            "case_name": "Case D", "citation": "D citation", "court": "Ninth Circuit",
            "jurisdiction": "Federal", "decision_date": "2003-01-01",
        },
    ]
    _install_fake_client(monkeypatch, rpc_data={"match_case_chunks": [], "keyword_search_case_chunks": sparse_rows})

    results = retrieve("some query")

    assert [chunk.id for chunk in results] == ["chunk-c1", "chunk-d1"]
    assert results[0].sparse_rank == 0.9
    assert results[0].dense_similarity is None


# =====================
# Fusion ordering
# =====================


def test_fusion_promotes_chunk_present_in_both_lists(monkeypatch):
    dense_rows = [
        {
            "id": "chunk-a1", "case_id": "case-a", "chunk_index": 0,
            "chunk_text": "text a", "similarity": 0.9,
            "case_name": "Case A", "citation": "A citation", "court": "Fifth Circuit",
            "decision_date": "2000-01-01",
        },
        {
            "id": "chunk-b1", "case_id": "case-b", "chunk_index": 0,
            "chunk_text": "text b", "similarity": 0.8,
            "case_name": "Case B", "citation": "B citation", "court": "Ninth Circuit",
            "decision_date": "2001-01-01",
        },
    ]
    sparse_rows = [
        {
            "id": "chunk-b1", "case_id": "case-b", "chunk_index": 0,
            "chunk_text": "text b", "rank": 0.95,
            "case_name": "Case B", "citation": "B citation", "court": "Ninth Circuit",
            "jurisdiction": "Federal", "decision_date": "2001-01-01",
        },
        {
            "id": "chunk-c1", "case_id": "case-c", "chunk_index": 0,
            "chunk_text": "text c", "rank": 0.5,
            "case_name": "Case C", "citation": "C citation", "court": "Fifth Circuit",
            "jurisdiction": "Federal", "decision_date": "2002-01-01",
        },
    ]
    table_data = {
        "cases": [
            {"id": "case-a", "jurisdiction": "Federal"},
            {"id": "case-b", "jurisdiction": "Federal"},
        ],
        "case_citations": [],
    }
    _install_fake_client(monkeypatch, rpc_data={"match_case_chunks": dense_rows, "keyword_search_case_chunks": sparse_rows}, table_data=table_data)

    results = retrieve("some query")
    ids = [chunk.id for chunk in results]

    # chunk-b1 ranks #2 in dense and #1 in sparse -- appearing in both
    # lists should push it above chunk-a1 (dense rank #1 only, no sparse hit).
    assert ids[0] == "chunk-b1"
    assert "chunk-a1" in ids
    assert "chunk-c1" in ids

    k = settings.retrieval_rrf_k
    by_id = {chunk.id: chunk for chunk in results}
    assert by_id["chunk-b1"].score == pytest.approx(1 / (k + 2) + 1 / (k + 1))
    assert by_id["chunk-a1"].score == pytest.approx(1 / (k + 1))
    assert by_id["chunk-c1"].score == pytest.approx(1 / (k + 2))


# =====================
# Citation-graph boost
# =====================


def test_citation_boost_applies_only_to_connected_candidates(monkeypatch):
    dense_rows = [
        {
            "id": "chunk-p", "case_id": "case-p", "chunk_index": 0,
            "chunk_text": "text p", "similarity": 0.9,
            "case_name": "Case P", "citation": "P citation", "court": "Fifth Circuit",
            "decision_date": "2000-01-01",
        },
        {
            "id": "chunk-q", "case_id": "case-q", "chunk_index": 0,
            "chunk_text": "text q", "similarity": 0.8,
            "case_name": "Case Q", "citation": "Q citation", "court": "Ninth Circuit",
            "decision_date": "2001-01-01",
        },
        {
            "id": "chunk-r", "case_id": "case-r", "chunk_index": 0,
            "chunk_text": "text r", "similarity": 0.7,
            "case_name": "Case R", "citation": "R citation", "court": "Ninth Circuit",
            "decision_date": "2002-01-01",
        },
    ]
    table_data = {
        "cases": [
            {"id": "case-p", "jurisdiction": "Federal"},
            {"id": "case-q", "jurisdiction": "Federal"},
            {"id": "case-r", "jurisdiction": "Federal"},
        ],
        # Only case-p <-> case-q are connected; case-r is an isolated candidate.
        "case_citations": [
            {"citing_case_id": "case-p", "cited_case_id": "case-q"},
        ],
    }
    _install_fake_client(monkeypatch, rpc_data={"match_case_chunks": dense_rows, "keyword_search_case_chunks": []}, table_data=table_data)

    results = retrieve("some query")
    by_id = {chunk.id: chunk for chunk in results}

    k = settings.retrieval_rrf_k
    boost = settings.retrieval_citation_boost
    assert by_id["chunk-p"].score == pytest.approx(1 / (k + 1) + boost)
    assert by_id["chunk-q"].score == pytest.approx(1 / (k + 2) + boost)
    assert by_id["chunk-r"].score == pytest.approx(1 / (k + 3))  # untouched, not connected


# =====================
# Structured filters
# =====================


def test_jurisdiction_filter_narrows_results(monkeypatch):
    dense_rows = [
        {
            "id": "chunk-fed", "case_id": "case-fed", "chunk_index": 0,
            "chunk_text": "federal text", "similarity": 0.9,
            "case_name": "Federal Case", "citation": "Fed citation", "court": "Fifth Circuit",
            "decision_date": "2000-01-01",
        },
        {
            "id": "chunk-state", "case_id": "case-state", "chunk_index": 0,
            "chunk_text": "state text", "similarity": 0.8,
            "case_name": "State Case", "citation": "State citation", "court": "Texas",
            "decision_date": "2001-01-01",
        },
    ]
    table_data = {
        "cases": [
            {"id": "case-fed", "jurisdiction": "Federal"},
            {"id": "case-state", "jurisdiction": "State"},
        ],
        "case_citations": [],
    }
    _install_fake_client(monkeypatch, rpc_data={"match_case_chunks": dense_rows, "keyword_search_case_chunks": []}, table_data=table_data)

    results = retrieve("some query", jurisdiction="Federal")

    assert [chunk.id for chunk in results] == ["chunk-fed"]


def test_date_range_filter_narrows_results(monkeypatch):
    dense_rows = [
        {
            "id": "chunk-old", "case_id": "case-old", "chunk_index": 0,
            "chunk_text": "old text", "similarity": 0.9,
            "case_name": "Old Case", "citation": "Old citation", "court": "Fifth Circuit",
            "decision_date": "1950-01-01",
        },
        {
            "id": "chunk-recent", "case_id": "case-recent", "chunk_index": 0,
            "chunk_text": "recent text", "similarity": 0.8,
            "case_name": "Recent Case", "citation": "Recent citation", "court": "Ninth Circuit",
            "decision_date": "2010-06-15",
        },
    ]
    table_data = {
        "cases": [
            {"id": "case-old", "jurisdiction": "Federal"},
            {"id": "case-recent", "jurisdiction": "Federal"},
        ],
        "case_citations": [],
    }
    _install_fake_client(monkeypatch, rpc_data={"match_case_chunks": dense_rows, "keyword_search_case_chunks": []}, table_data=table_data)

    results = retrieve("some query", date_from="2000-01-01", date_to="2020-01-01")

    assert [chunk.id for chunk in results] == ["chunk-recent"]


# =====================
# Integration (live Supabase, ~2 hand-picked queries)
# =====================


@pytest.mark.integration
class TestHybridRetrievalIntegration:
    def _check_credentials(self):
        assert settings.supabase_url and settings.supabase_service_role_key, "Missing SUPABASE environment variables"

    def test_live_fourth_amendment_query(self):
        self._check_credentials()
        results = retrieve("Fourth amendment unreasonable search and seizure of vehicle without warrant")

        assert len(results) > 0
        scores = [chunk.score for chunk in results]
        assert scores == sorted(scores, reverse=True)
        print(f"\nTop result: {results[0].case_name} ({results[0].citation}) score={results[0].score:.4f}")

    def test_live_qualified_immunity_query(self):
        self._check_credentials()
        results = retrieve("qualified immunity for police officers in the Fifth Circuit")

        assert len(results) > 0
        scores = [chunk.score for chunk in results]
        assert scores == sorted(scores, reverse=True)
        print(f"\nTop result: {results[0].case_name} ({results[0].citation}) score={results[0].score:.4f}")
