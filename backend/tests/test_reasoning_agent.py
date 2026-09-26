"""
Module 5 -- Structured Reasoning Agent tests.

Mocked suite (default CI, no API cost): swaps reasoning_agent's model for
a deterministic pydantic_ai TestModel, same pattern as
tests/test_query_analyzer.py. Since citation extraction happens outside
the LLM call (see reasoning_agent.py docstring), these tests exercise the
real _extract_citations logic against a mocked ReasoningDraft, not a
second mock of the citations themselves.

Integration tests at the bottom require live Openrouter + Supabase
credentials and run the real Module 3 -> 3.5 -> 4 -> 5 chain; excluded
from default CI (`pytest -m "not integration"`).
"""

import pytest
from pydantic_ai.models.test import TestModel

from app.services.reasoning_agent import (
    ReasoningResult,
    generate_reasoning,
    reasoning_agent,
)
from app.services.retrieval import CaseChunk


def _make_chunk(case_id: str, case_name: str, citation: str, chunk_text: str = "excerpt") -> CaseChunk:
    return CaseChunk(
        id=f"chunk-{case_id}",
        case_id=case_id,
        chunk_index=0,
        chunk_text=chunk_text,
        case_name=case_name,
        citation=citation,
    )


def _run_with_mock(query, valid_chunks, flagged_premise=None, **draft_kwargs) -> ReasoningResult:
    defaults = {
        "insufficient_sources": False,
        "insufficient_sources_reason": None,
        "issue": None,
        "rule": None,
        "application": None,
        "conclusion": None,
        "addressed_false_premise": False,
    }
    defaults.update(draft_kwargs)
    with reasoning_agent.override(model=TestModel(custom_output_args=defaults)):
        return generate_reasoning(query, valid_chunks, flagged_premise)


# =====================
# Standard IRAC generation with citations
# =====================


def test_standard_irac_with_citations():
    chunks = [
        _make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1"),
        _make_chunk("case-b", "United States v. Ross", "456 U.S. 798"),
    ]
    result = _run_with_mock(
        "What standard governs a warrantless vehicle search?",
        chunks,
        issue="Whether police may search a vehicle without a warrant.",
        rule="An officer may conduct a limited search on reasonable suspicion [1].",
        application="Here, the automobile exception permits a full search on probable cause [2].",
        conclusion="The search is lawful [1][2].",
    )

    assert result.insufficient_sources is False
    assert result.insufficient_sources_reason is None
    assert result.issue is not None
    assert result.rule is not None
    assert result.application is not None
    assert result.conclusion is not None
    assert result.addressed_false_premise is False

    assert [c.marker for c in result.citations] == [1, 2]
    assert result.citations[0].case_id == "case-a"
    assert result.citations[0].case_name == "Terry v. Ohio"
    assert result.citations[0].citation == "392 U.S. 1"
    assert result.citations[0].chunk_id == "chunk-case-a"
    assert result.citations[1].case_id == "case-b"


# =====================
# False-premise handling
# =====================


def test_false_premise_addressed():
    chunks = [_make_chunk("case-c", "Miranda v. Arizona", "384 U.S. 436")]
    result = _run_with_mock(
        "Given that Miranda no longer requires warnings, what should officers say?",
        chunks,
        flagged_premise="Miranda v. Arizona has not been overruled and still requires warnings.",
        application="Miranda remains good law and still requires the standard warnings [1].",
        addressed_false_premise=True,
    )

    assert result.insufficient_sources is False
    assert result.addressed_false_premise is True
    assert result.issue is None
    assert result.rule is None
    assert result.conclusion is None
    assert [c.marker for c in result.citations] == [1]
    assert result.citations[0].case_name == "Miranda v. Arizona"


def test_addressed_false_premise_forced_false_when_no_premise_flagged():
    """Defensive guard: even if the model mistakenly sets
    addressed_false_premise=True with no flagged_premise, the public
    result must not claim a false premise was addressed."""
    chunks = [_make_chunk("case-a", "Case A", "1 U.S. 1")]
    result = _run_with_mock(
        "What is the rule in Case A?",
        chunks,
        flagged_premise=None,
        rule="Some rule [1].",
        addressed_false_premise=True,
    )
    assert result.addressed_false_premise is False


# =====================
# Insufficient sources
# =====================


def test_insufficient_sources_on_empty_valid_chunks():
    # No model override -- empty valid_chunks must short-circuit before
    # any LLM call is made.
    result = generate_reasoning("What is the rule in a case with no sources?", [])
    assert result.insufficient_sources is True
    assert result.insufficient_sources_reason
    assert result.issue is None
    assert result.rule is None
    assert result.application is None
    assert result.conclusion is None
    assert result.citations == []
    assert result.addressed_false_premise is False


def test_insufficient_sources_when_model_flags_despite_chunks_present():
    chunks = [_make_chunk("case-a", "Unrelated Case", "1 U.S. 1", chunk_text="Discusses an unrelated topic.")]
    result = _run_with_mock(
        "What is the holding on an entirely unrelated area of tax law?",
        chunks,
        insufficient_sources=True,
        insufficient_sources_reason="The provided sources concern an unrelated area of law.",
        rule="This should be discarded [1].",
    )

    assert result.insufficient_sources is True
    assert result.insufficient_sources_reason == "The provided sources concern an unrelated area of law."
    assert result.issue is None
    assert result.rule is None
    assert result.application is None
    assert result.conclusion is None
    assert result.citations == []


# =====================
# Multi-citation-per-claim / multi-claim-per-citation
# =====================


def test_citation_mapping_is_not_forced_one_to_one():
    chunks = [
        _make_chunk("case-a", "Case A", "1 U.S. 1"),
        _make_chunk("case-b", "Case B", "2 U.S. 2"),
        _make_chunk("case-c", "Case C", "3 U.S. 3"),
    ]
    result = _run_with_mock(
        "Multi-source query",
        chunks,
        issue="Issue text, no citations needed here.",
        rule="A single claim relying on two sources at once [1][2].",
        application="Source 1 supports a second, different claim here too [1].",
        conclusion="A claim citing all three sources [1][2][3].",
    )

    # Marker 1 supports three separate claims but appears once in citations;
    # marker 2 appears in two claims; nothing is forced 1:1 either direction.
    assert [c.marker for c in result.citations] == [1, 2, 3]
    assert len({c.marker for c in result.citations}) == 3


def test_out_of_range_marker_is_silently_dropped():
    chunks = [_make_chunk("case-a", "Case A", "1 U.S. 1")]
    result = _run_with_mock(
        "Single-source query",
        chunks,
        rule="Cites the real source [1] and a hallucinated one [7].",
    )
    assert [c.marker for c in result.citations] == [1]


def test_out_of_range_marker_text_is_stripped_from_output():
    """Regression test: an out-of-range marker must not just be excluded
    from `citations` -- its literal "[N]" text must also be removed from
    the displayed field, so no dangling unbacked marker is shown. Valid,
    in-range markers must survive untouched."""
    chunks = [
        _make_chunk("case-a", "Case A", "1 U.S. 1"),
        _make_chunk("case-b", "Case B", "2 U.S. 2"),
    ]
    result = _run_with_mock(
        "Multi-source query with a hallucinated marker",
        chunks,
        issue="Whether the rule applies here [9].",
        rule="A real claim backed by a real source [1].",
        application="Another claim citing two real sources [1][2] and a fake one [7].",
        conclusion="Final answer with no citation issues here.",
    )

    all_text = " ".join(
        text for text in (result.issue, result.rule, result.application, result.conclusion) if text
    )
    assert "[9]" not in all_text
    assert "[7]" not in all_text
    assert "[1]" in result.rule
    assert "[1]" in result.application
    assert "[2]" in result.application
    assert result.issue == "Whether the rule applies here."

    assert [c.marker for c in result.citations] == [1, 2]


def test_generate_reasoning_returns_reasoning_result_instance():
    chunks = [_make_chunk("case-a", "Case A", "1 U.S. 1")]
    result = _run_with_mock("Sanity check query", chunks, rule="Some rule [1].")
    assert isinstance(result, ReasoningResult)


# =====================
# Integration (live Openrouter + Supabase, ~5 hand-picked cases)
# =====================


@pytest.mark.integration
class TestReasoningAgentIntegration:
    def _check_credentials(self):
        from app.core.config import settings

        assert settings.llm_provider_api_key, "Missing LLM_PROVIDER_API_KEY environment variable"
        assert settings.supabase_url and settings.supabase_service_role_key, "Missing SUPABASE environment variables"

    def _real_valid_chunks(self, query: str):
        from app.services.reranker import rerank
        from app.services.retrieval import retrieve
        from app.services.validity_checker import check_validity

        candidates = retrieve(query)
        reranked = rerank(query, candidates)
        return check_validity(reranked).valid_chunks

    def test_live_factual_fourth_amendment_vehicle_search(self):
        self._check_credentials()
        query = "What standard governs a warrantless search of a vehicle incident to arrest?"
        valid_chunks = self._real_valid_chunks(query)
        result = generate_reasoning(query, valid_chunks)
        print(f"\ninsufficient_sources={result.insufficient_sources} citations={len(result.citations)}")
        if not result.insufficient_sources:
            assert result.issue and result.rule and result.application and result.conclusion
            assert result.citations
            for citation in result.citations:
                assert f"[{citation.marker}]" in (
                    (result.rule or "") + (result.application or "") + (result.conclusion or "")
                )

    def test_live_factual_qualified_immunity(self):
        self._check_credentials()
        query = "What must a plaintiff show to overcome a qualified immunity defense?"
        valid_chunks = self._real_valid_chunks(query)
        result = generate_reasoning(query, valid_chunks)
        print(f"\ninsufficient_sources={result.insufficient_sources} citations={len(result.citations)}")
        if not result.insufficient_sources:
            assert result.issue and result.rule and result.application and result.conclusion
            assert result.citations

    def test_live_factual_section_1983(self):
        self._check_credentials()
        query = "What are the elements of a Section 1983 claim against a state actor?"
        valid_chunks = self._real_valid_chunks(query)
        result = generate_reasoning(query, valid_chunks)
        print(f"\ninsufficient_sources={result.insufficient_sources} citations={len(result.citations)}")
        if not result.insufficient_sources:
            assert result.citations

    def test_live_false_premise_exclusionary_rule(self):
        self._check_credentials()
        query = (
            "Since the exclusionary rule no longer requires suppressing evidence from an "
            "unreasonable search, what should officers do differently?"
        )
        flagged_premise = "The exclusionary rule still requires suppression of evidence obtained via an unreasonable search."
        valid_chunks = self._real_valid_chunks(query)
        result = generate_reasoning(query, valid_chunks, flagged_premise=flagged_premise)
        print(
            f"\ninsufficient_sources={result.insufficient_sources} "
            f"addressed_false_premise={result.addressed_false_premise} application={result.application}"
        )
        if not result.insufficient_sources:
            assert result.addressed_false_premise is True
            assert result.application

    def test_live_false_premise_qualified_immunity(self):
        self._check_credentials()
        query = (
            "Given that qualified immunity has been abolished nationwide, how should officers "
            "now be held personally liable?"
        )
        flagged_premise = "Qualified immunity has not been abolished nationwide and still applies in most circuits."
        valid_chunks = self._real_valid_chunks(query)
        result = generate_reasoning(query, valid_chunks, flagged_premise=flagged_premise)
        print(
            f"\ninsufficient_sources={result.insufficient_sources} "
            f"addressed_false_premise={result.addressed_false_premise} application={result.application}"
        )
        if not result.insufficient_sources:
            assert result.addressed_false_premise is True
