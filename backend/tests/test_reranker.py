"""
Module 3.5 -- Reranker tests.

Mocked suite (default CI): a fake CrossEncoder stands in for the real
sentence-transformers model, returning deterministic canned scores so
sort-and-narrow logic is exercised without loading the actual model.

Integration tests: marked with @pytest.mark.integration (excluded from
default CI), load the real BAAI/bge-reranker-base model and confirm it
sensibly reorders a hand-picked set of candidates for known queries.
"""

import pytest

from app.core.config import settings
from app.services.reranker import rerank
from app.services.retrieval import CaseChunk


def _make_chunk(
    id: str,
    case_id: str,
    chunk_text: str,
    case_name: str = "Test Case",
    citation: str = "Test Citation",
) -> CaseChunk:
    """Helper to construct test chunks with minimal boilerplate."""
    return CaseChunk(
        id=id,
        case_id=case_id,
        chunk_index=0,
        chunk_text=chunk_text,
        case_name=case_name,
        citation=citation,
        dense_similarity=0.8,
        sparse_rank=1.0,
        score=0.5,
    )


# =====================
# Mocked tests (default CI)
# =====================


def test_rerank_sorts_by_score_descending(monkeypatch):
    """Verify rerank sorts candidates by rerank_score descending."""
    candidates = [
        _make_chunk("c1", "case-1", "First chunk text"),
        _make_chunk("c2", "case-2", "Second chunk text"),
        _make_chunk("c3", "case-3", "Third chunk text"),
    ]

    class MockModel:
        def predict(self, pairs):
            return [0.3, 0.9, 0.5]

    mock_model = MockModel()
    monkeypatch.setattr("app.services.reranker._get_reranker_model", lambda: mock_model)

    result = rerank("test query", candidates)

    assert [chunk.rerank_score for chunk in result] == [0.9, 0.5, 0.3]
    assert [chunk.id for chunk in result] == ["c2", "c3", "c1"]


def test_rerank_narrows_to_top_n(monkeypatch):
    """Verify rerank returns only top_n results."""
    candidates = [
        _make_chunk("c1", "case-1", "First chunk"),
        _make_chunk("c2", "case-2", "Second chunk"),
        _make_chunk("c3", "case-3", "Third chunk"),
        _make_chunk("c4", "case-4", "Fourth chunk"),
        _make_chunk("c5", "case-5", "Fifth chunk"),
    ]

    class MockModel:
        def predict(self, pairs):
            return [0.5, 0.4, 0.3, 0.2, 0.1]

    mock_model = MockModel()
    monkeypatch.setattr("app.services.reranker._get_reranker_model", lambda: mock_model)

    result = rerank("test query", candidates, top_n=2)

    assert len(result) == 2
    assert [chunk.id for chunk in result] == ["c1", "c2"]


def test_rerank_defaults_to_config_top_n(monkeypatch):
    """Verify rerank defaults to config setting when top_n is None."""
    candidates = [
        _make_chunk("c1", "case-1", "First"),
        _make_chunk("c2", "case-2", "Second"),
        _make_chunk("c3", "case-3", "Third"),
        _make_chunk("c4", "case-4", "Fourth"),
        _make_chunk("c5", "case-5", "Fifth"),
    ]

    class MockModel:
        def predict(self, pairs):
            return [0.5, 0.4, 0.3, 0.2, 0.1]

    mock_model = MockModel()
    monkeypatch.setattr("app.services.reranker._get_reranker_model", lambda: mock_model)

    result = rerank("test query", candidates, top_n=None)

    assert len(result) == min(5, len(candidates))


def test_rerank_preserves_existing_fields(monkeypatch):
    """Verify rerank_score is set without overwriting existing fields."""
    chunk = _make_chunk("c1", "case-1", "Test text")
    chunk.dense_similarity = 0.95
    chunk.sparse_rank = 2.0
    chunk.score = 0.75

    class MockModel:
        def predict(self, pairs):
            return [0.85]

    mock_model = MockModel()
    monkeypatch.setattr("app.services.reranker._get_reranker_model", lambda: mock_model)

    result = rerank("test query", [chunk])

    assert len(result) == 1
    assert result[0].rerank_score == 0.85
    assert result[0].dense_similarity == 0.95
    assert result[0].sparse_rank == 2.0
    assert result[0].score == 0.75


def test_rerank_empty_candidates_returns_empty(monkeypatch):
    """Verify rerank handles empty candidate list gracefully."""
    result = rerank("test query", [])
    assert result == []


def test_rerank_changes_order_vs_input(monkeypatch):
    """Verify reranking actually reorders (not a no-op passthrough)."""
    candidates = [
        _make_chunk("c1", "case-1", "Most relevant semantic match"),
        _make_chunk("c2", "case-2", "Less relevant match"),
        _make_chunk("c3", "case-3", "Slightly relevant"),
    ]

    class MockModel:
        def predict(self, pairs):
            return [0.5, 0.3, 0.9]

    mock_model = MockModel()
    monkeypatch.setattr("app.services.reranker._get_reranker_model", lambda: mock_model)

    result = rerank("test query", candidates)

    input_order = [chunk.id for chunk in candidates]
    result_order = [chunk.id for chunk in result]
    assert input_order != result_order
    assert result_order == ["c3", "c1", "c2"]


def test_rerank_batches_all_candidates_in_one_call(monkeypatch):
    """Verify rerank uses a single CrossEncoder.predict() call, not per-item."""
    candidates = [
        _make_chunk("c1", "case-1", "Text 1"),
        _make_chunk("c2", "case-2", "Text 2"),
        _make_chunk("c3", "case-3", "Text 3"),
    ]

    call_count = [0]

    class MockModel:
        def predict(self, pairs):
            call_count[0] += 1
            return [0.5, 0.6, 0.4]

    mock_model = MockModel()
    monkeypatch.setattr("app.services.reranker._get_reranker_model", lambda: mock_model)

    rerank("test query", candidates)

    assert call_count[0] == 1, "Expected exactly one CrossEncoder.predict() call"


# =====================
# Integration (live BAAI/bge-reranker-base model)
# =====================


@pytest.mark.integration
class TestRerankerIntegration:
    def _check_credentials(self):
        assert (
            settings.supabase_url and settings.supabase_service_role_key
        ), "Missing SUPABASE environment variables"

    def test_live_rerank_fourth_amendment_query(self):
        """Rerank a hand-picked set of chunks for a Fourth Amendment query."""
        self._check_credentials()

        query = "Fourth amendment unreasonable search and seizure vehicle warrant"

        # Create realistic chunk candidates with varied relevance
        candidates = [
            CaseChunk(
                id="chunk-1",
                case_id="case-1",
                chunk_index=0,
                chunk_text="The Fourth Amendment protects against unreasonable searches and seizures.",
                case_name="Terry v. Ohio",
                citation="392 U.S. 1",
                dense_similarity=0.85,
                sparse_rank=1.0,
                score=0.70,
            ),
            CaseChunk(
                id="chunk-2",
                case_id="case-2",
                chunk_index=0,
                chunk_text="A vehicle may be subject to search incident to lawful arrest.",
                case_name="New York v. Belton",
                citation="441 U.S. 144",
                dense_similarity=0.82,
                sparse_rank=2.0,
                score=0.68,
            ),
            CaseChunk(
                id="chunk-3",
                case_id="case-3",
                chunk_index=0,
                chunk_text="The right to due process is fundamental in civil litigation.",
                case_name="Mathews v. Eldridge",
                citation="424 U.S. 319",
                dense_similarity=0.60,
                sparse_rank=5.0,
                score=0.55,
            ),
            CaseChunk(
                id="chunk-4",
                case_id="case-4",
                chunk_index=0,
                chunk_text="Warrantless searches of vehicles are generally unconstitutional.",
                case_name="Arizona v. Hicks",
                citation="480 U.S. 321",
                dense_similarity=0.88,
                sparse_rank=3.0,
                score=0.72,
            ),
        ]

        result = rerank(query, candidates, top_n=3)

        assert len(result) == 3
        # Verify rerank_score is set on all results
        assert all(chunk.rerank_score is not None for chunk in result)
        # Verify results are sorted by rerank_score descending
        scores = [chunk.rerank_score for chunk in result]
        assert scores == sorted(scores, reverse=True)
        # Print for manual inspection
        print(f"\nReranked results for '{query}':")
        for i, chunk in enumerate(result, 1):
            print(f"  {i}. {chunk.case_name}: rerank_score={chunk.rerank_score:.4f}")

    def test_live_rerank_qualified_immunity_query(self):
        """Rerank a hand-picked set for a qualified immunity query."""
        self._check_credentials()

        query = "qualified immunity police officers Fifth Circuit"

        candidates = [
            CaseChunk(
                id="chunk-qi-1",
                case_id="case-qi-1",
                chunk_index=0,
                chunk_text="Qualified immunity shields officers from liability unless they violated a clearly established right.",
                case_name="Harlow v. Fitzgerald",
                citation="457 U.S. 800",
                dense_similarity=0.90,
                sparse_rank=1.0,
                score=0.75,
            ),
            CaseChunk(
                id="chunk-qi-2",
                case_id="case-qi-2",
                chunk_index=0,
                chunk_text="Miranda warnings are required before custodial interrogation.",
                case_name="Miranda v. Arizona",
                citation="384 U.S. 436",
                dense_similarity=0.65,
                sparse_rank=10.0,
                score=0.50,
            ),
            CaseChunk(
                id="chunk-qi-3",
                case_id="case-qi-3",
                chunk_index=0,
                chunk_text="In the Fifth Circuit, qualified immunity applies to police use of force claims.",
                case_name="Example Fifth Circuit Case",
                citation="999 F.3d 100",
                dense_similarity=0.87,
                sparse_rank=2.0,
                score=0.73,
            ),
        ]

        result = rerank(query, candidates, top_n=2)

        assert len(result) == 2
        assert all(chunk.rerank_score is not None for chunk in result)
        scores = [chunk.rerank_score for chunk in result]
        assert scores == sorted(scores, reverse=True)
        print(f"\nReranked results for '{query}':")
        for i, chunk in enumerate(result, 1):
            print(f"  {i}. {chunk.case_name}: rerank_score={chunk.rerank_score:.4f}")
