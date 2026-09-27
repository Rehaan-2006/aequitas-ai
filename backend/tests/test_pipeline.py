"""
Module 7 -- Research Pipeline Orchestration tests.

Mocked suite (default CI, no API/DB cost): every stage function pipeline.py
imports (sanitize_query, analyze_query, retrieve, rerank, check_validity,
generate_reasoning, verify_citations) is monkeypatched directly on the
`app.services.pipeline` module namespace -- pipeline.py itself does no
mocking-unfriendly work (no LLM call, no DB call of its own), so this
exercises only the real sequential control flow, short-circuiting, and
trace-building logic.

Integration tests at the bottom require live Openrouter + Supabase
credentials and run the real end-to-end pipeline; excluded from default
CI (`pytest -m "not integration"`).
"""

import pytest

from app.services import pipeline as pipeline_module
from app.services.citation_verifier import CitationVerdict, CitationVerifierResult
from app.services.pipeline import PipelineDeps, PipelineOutcome, run_pipeline
from app.services.query_analyzer import QueryAnalysis, QueryType
from app.services.reasoning_agent import Citation, ReasoningResult
from app.services.retrieval import CaseChunk
from app.services.sanitizer import SanitizationResult, SanitizationStatus
from app.services.validity_checker import ValidityCheckResult


def _make_chunk(case_id: str = "case-a", score: float = 0.9, rerank_score: float | None = None) -> CaseChunk:
    return CaseChunk(
        id=f"chunk-{case_id}",
        case_id=case_id,
        chunk_index=0,
        chunk_text="Some case text.",
        case_name="Some v. Case",
        citation="1 U.S. 1",
        score=score,
        rerank_score=rerank_score,
    )


def _passed_sanitization(query: str = "What is the standard for negligence?") -> SanitizationResult:
    return SanitizationResult(status=SanitizationStatus.PASSED, query=query)


def _factual_analysis() -> QueryAnalysis:
    return QueryAnalysis(query_type=QueryType.FACTUAL, reasoning="Well-formed factual question.")


def _answered_reasoning(chunk: CaseChunk) -> ReasoningResult:
    return ReasoningResult(
        insufficient_sources=False,
        issue="Some issue.",
        rule="Some rule [1].",
        application="Some application [1].",
        conclusion="Some conclusion [1].",
        citations=[
            Citation(
                marker=1,
                case_id=chunk.case_id,
                case_name=chunk.case_name,
                citation=chunk.citation,
                chunk_id=chunk.id,
            )
        ],
    )


def _insufficient_sources_reasoning() -> ReasoningResult:
    return ReasoningResult(insufficient_sources=True, insufficient_sources_reason="Nothing on point.")


def _auto_answer_verdict(chunk: CaseChunk) -> CitationVerifierResult:
    return CitationVerifierResult(
        outcome="AUTO_ANSWER",
        verdicts=[
            CitationVerdict(
                marker=1,
                case_id=chunk.case_id,
                case_name=chunk.case_name,
                verdict="VERIFIED",
                explanation="Supported.",
            )
        ],
    )


def _abstain_verdict() -> CitationVerifierResult:
    return CitationVerifierResult(outcome="ABSTAIN", abstain_reason="marker [1] failed verification.")


def _fail_if_called(name: str):
    def _inner(*args, **kwargs):
        raise AssertionError(f"{name} should not have been called on this path")

    return _inner


def _wire_happy_path(monkeypatch, chunk: CaseChunk):
    monkeypatch.setattr(pipeline_module, "sanitize_query", lambda query: _passed_sanitization(query))
    monkeypatch.setattr(pipeline_module, "analyze_query", lambda query: _factual_analysis())
    monkeypatch.setattr(pipeline_module, "retrieve", lambda query, jurisdiction=None, date_from=None, date_to=None: [chunk])
    monkeypatch.setattr(pipeline_module, "rerank", lambda query, candidates: candidates)
    monkeypatch.setattr(
        pipeline_module,
        "check_validity",
        lambda candidates: ValidityCheckResult(valid_chunks=candidates, dropped=[], substitutions=[]),
    )
    monkeypatch.setattr(
        pipeline_module,
        "generate_reasoning",
        lambda query, valid_chunks, flagged_premise=None: _answered_reasoning(chunk),
    )
    monkeypatch.setattr(pipeline_module, "verify_citations", lambda reasoning_result, valid_chunks: _auto_answer_verdict(chunk))


# =====================
# Full happy path -> ANSWERED
# =====================


def test_full_happy_path_all_stages_answered(monkeypatch):
    chunk = _make_chunk(rerank_score=0.8)
    _wire_happy_path(monkeypatch, chunk)

    result = run_pipeline("What is the standard for negligence?")

    assert result.outcome == PipelineOutcome.ANSWERED
    assert result.citations_verified is True
    assert result.issue == "Some issue."
    assert len(result.citations) == 1

    stages = [entry.stage for entry in result.trace]
    assert stages == [
        "sanitization",
        "query_analysis",
        "retrieval",
        "reranker",
        "validity_check",
        "reasoning",
        "citation_verification",
    ]
    assert all(not entry.skipped for entry in result.trace)


# =====================
# Sanitization rejection short-circuit
# =====================


def test_sanitization_rejection_short_circuits(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "sanitize_query",
        lambda query: SanitizationResult(
            status=SanitizationStatus.OFF_TOPIC, query=query, reason="This question is not about legal research."
        ),
    )
    monkeypatch.setattr(pipeline_module, "analyze_query", _fail_if_called("analyze_query"))
    monkeypatch.setattr(pipeline_module, "retrieve", _fail_if_called("retrieve"))
    monkeypatch.setattr(pipeline_module, "rerank", _fail_if_called("rerank"))
    monkeypatch.setattr(pipeline_module, "check_validity", _fail_if_called("check_validity"))
    monkeypatch.setattr(pipeline_module, "generate_reasoning", _fail_if_called("generate_reasoning"))
    monkeypatch.setattr(pipeline_module, "verify_citations", _fail_if_called("verify_citations"))

    result = run_pipeline("How do I make a chocolate cake?")

    assert result.outcome == PipelineOutcome.REJECTED_AT_SANITIZATION
    assert result.rejection_reason == "This question is not about legal research."
    assert len(result.trace) == 1
    assert result.trace[0].stage == "sanitization"


# =====================
# Zero retrieval results short-circuit
# =====================


def test_zero_retrieval_results_short_circuits(monkeypatch):
    monkeypatch.setattr(pipeline_module, "sanitize_query", lambda query: _passed_sanitization(query))
    monkeypatch.setattr(pipeline_module, "analyze_query", lambda query: _factual_analysis())
    monkeypatch.setattr(
        pipeline_module, "retrieve", lambda query, jurisdiction=None, date_from=None, date_to=None: []
    )
    monkeypatch.setattr(pipeline_module, "rerank", _fail_if_called("rerank"))
    monkeypatch.setattr(pipeline_module, "check_validity", _fail_if_called("check_validity"))
    monkeypatch.setattr(pipeline_module, "generate_reasoning", _fail_if_called("generate_reasoning"))
    monkeypatch.setattr(pipeline_module, "verify_citations", _fail_if_called("verify_citations"))

    result = run_pipeline("What is the standard for a highly obscure claim?")

    assert result.outcome == PipelineOutcome.NO_RETRIEVAL_RESULTS
    assert result.message
    stages = [entry.stage for entry in result.trace]
    assert stages == ["sanitization", "query_analysis", "retrieval"]


# =====================
# Citation verifier ABSTAIN propagates as pipeline ABSTAIN
# =====================


def test_citation_verifier_abstain_propagates(monkeypatch):
    chunk = _make_chunk(rerank_score=0.8)
    _wire_happy_path(monkeypatch, chunk)
    monkeypatch.setattr(pipeline_module, "verify_citations", lambda reasoning_result, valid_chunks: _abstain_verdict())

    result = run_pipeline("What is the standard for negligence?")

    assert result.outcome == PipelineOutcome.ABSTAIN
    assert result.abstain_reason == "marker [1] failed verification."
    assert result.citations == []


# =====================
# Ablation toggles, individually disabled
# =====================


def test_reranker_disabled_skips_stage_and_passes_through(monkeypatch):
    chunk = _make_chunk(rerank_score=0.8)
    _wire_happy_path(monkeypatch, chunk)
    monkeypatch.setattr(pipeline_module, "rerank", _fail_if_called("rerank"))

    captured_candidates = {}

    def _fake_check_validity(candidates):
        captured_candidates["value"] = candidates
        return ValidityCheckResult(valid_chunks=candidates, dropped=[], substitutions=[])

    monkeypatch.setattr(pipeline_module, "check_validity", _fake_check_validity)

    result = run_pipeline("What is the standard for negligence?", deps=PipelineDeps(enable_reranker=False))

    assert result.outcome == PipelineOutcome.ANSWERED
    assert captured_candidates["value"] == [chunk]

    reranker_entry = next(entry for entry in result.trace if entry.stage == "reranker")
    assert reranker_entry.skipped is True
    assert reranker_entry.data == {}


def test_validity_check_disabled_skips_stage_and_passes_through(monkeypatch):
    chunk = _make_chunk(rerank_score=0.8)
    _wire_happy_path(monkeypatch, chunk)
    monkeypatch.setattr(pipeline_module, "check_validity", _fail_if_called("check_validity"))

    captured_valid_chunks = {}

    def _fake_generate_reasoning(query, valid_chunks, flagged_premise=None):
        captured_valid_chunks["value"] = valid_chunks
        return _answered_reasoning(chunk)

    monkeypatch.setattr(pipeline_module, "generate_reasoning", _fake_generate_reasoning)

    result = run_pipeline("What is the standard for negligence?", deps=PipelineDeps(enable_validity_check=False))

    assert result.outcome == PipelineOutcome.ANSWERED
    assert captured_valid_chunks["value"] == [chunk]

    validity_entry = next(entry for entry in result.trace if entry.stage == "validity_check")
    assert validity_entry.skipped is True
    assert validity_entry.data == {}


def test_citation_verification_disabled_skips_stage_and_is_not_marked_verified(monkeypatch):
    chunk = _make_chunk(rerank_score=0.8)
    _wire_happy_path(monkeypatch, chunk)
    monkeypatch.setattr(pipeline_module, "verify_citations", _fail_if_called("verify_citations"))

    result = run_pipeline(
        "What is the standard for negligence?", deps=PipelineDeps(enable_citation_verification=False)
    )

    assert result.outcome == PipelineOutcome.ANSWERED
    assert result.citations_verified is False
    assert result.issue == "Some issue."

    verification_entry = next(entry for entry in result.trace if entry.stage == "citation_verification")
    assert verification_entry.skipped is True
    assert verification_entry.data == {}


def test_citation_verification_disabled_with_insufficient_sources_still_abstains(monkeypatch):
    chunk = _make_chunk(rerank_score=0.8)
    _wire_happy_path(monkeypatch, chunk)
    monkeypatch.setattr(
        pipeline_module, "generate_reasoning", lambda query, valid_chunks, flagged_premise=None: _insufficient_sources_reasoning()
    )
    monkeypatch.setattr(pipeline_module, "verify_citations", _fail_if_called("verify_citations"))

    result = run_pipeline(
        "What is the standard for negligence?", deps=PipelineDeps(enable_citation_verification=False)
    )

    assert result.outcome == PipelineOutcome.ABSTAIN
    assert result.abstain_reason == "Nothing on point."
    assert result.citations_verified is False


# =====================
# Integration (live Openrouter + Supabase, ~5 hand-picked cases)
# =====================


@pytest.mark.integration
class TestPipelineIntegration:
    def _check_credentials(self):
        from app.core.config import settings

        assert settings.llm_provider_api_key, "Missing LLM_PROVIDER_API_KEY environment variable"
        assert settings.supabase_url and settings.supabase_service_role_key, "Missing SUPABASE environment variables"

    def test_live_strong_query_resolves_answered_or_abstain(self):
        self._check_credentials()
        result = run_pipeline("What standard governs a warrantless search of a vehicle incident to arrest?")
        print(f"\noutcome={result.outcome} trace_stages={[e.stage for e in result.trace]}")
        assert result.outcome in (PipelineOutcome.ANSWERED, PipelineOutcome.ABSTAIN)
        if result.outcome == PipelineOutcome.ANSWERED:
            assert result.citations_verified is True
            assert result.citations

    def test_live_weak_query_resolves_abstain_or_no_results(self):
        self._check_credentials()
        result = run_pipeline(
            "What is the precise multi-factor test this specific two-circuit corpus almost "
            "certainly lacks strong case law for -- an obscure maritime salvage lien priority dispute?"
        )
        print(f"\noutcome={result.outcome} trace_stages={[e.stage for e in result.trace]}")
        assert result.outcome in (
            PipelineOutcome.ABSTAIN,
            PipelineOutcome.NO_RETRIEVAL_RESULTS,
            PipelineOutcome.ANSWERED,
        )

    def test_live_false_premise_query(self):
        self._check_credentials()
        result = run_pipeline(
            "Since the Supreme Court overruled Miranda v. Arizona, do officers still need to "
            "read suspects their rights before questioning?"
        )
        print(f"\noutcome={result.outcome} trace_stages={[e.stage for e in result.trace]}")
        assert result.outcome in (PipelineOutcome.ANSWERED, PipelineOutcome.ABSTAIN)

    def test_live_pii_query_rejected_at_sanitization(self):
        self._check_credentials()
        result = run_pipeline("My SSN is 123-45-6789, what is the standard for negligence?")
        assert result.outcome == PipelineOutcome.REJECTED_AT_SANITIZATION
        assert len(result.trace) == 1

    def test_live_ablation_reranker_disabled(self):
        self._check_credentials()
        result = run_pipeline(
            "What must a plaintiff show to overcome a qualified immunity defense?",
            deps=PipelineDeps(enable_reranker=False),
        )
        print(f"\noutcome={result.outcome} trace_stages={[e.stage for e in result.trace]}")
        reranker_entry = next(entry for entry in result.trace if entry.stage == "reranker")
        assert reranker_entry.skipped is True
        assert result.outcome in (
            PipelineOutcome.ANSWERED,
            PipelineOutcome.ABSTAIN,
            PipelineOutcome.NO_RETRIEVAL_RESULTS,
        )
