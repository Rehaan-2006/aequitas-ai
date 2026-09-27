# NOTE(scope): pure control flow, no LLM call of its own. Every stage's
# actual logic (sanitization, classification, retrieval, reranking,
# validity checking, reasoning, citation verification) lives exactly
# once in its own module and is only ever called here, never
# reimplemented. This module also does not implement conversation
# memory/multi-turn state (no thread storage layer exists yet) or a
# "request more candidates from retrieval" retry/expansion loop when
# validity checking or reasoning thin the result set heavily -- both
# are explicitly out of scope for this module (see the comment at the
# relevant call site below for the retry point specifically).
"""
Module 7 -- Research Pipeline Orchestration.

Wires Modules 1.5 through 6 together sequentially into a single entry
point (`run_pipeline`), short-circuiting explicitly at each documented
failure point, and produces a structured per-stage trace log intended
to feed the future ablation study (Module 16) -- structured stage
output, not a prose summary.

Per-stage ablation toggles (`PipelineDeps`) live here for the first
time in the codebase: Modules 3.5, 4, and 6 each explicitly deferred
the decision of whether/when their stage can be skipped to this
orchestration layer, since only the orchestrator has enough context to
decide what "skip this stage" should mean for the stages downstream of
it (e.g. skipping the reranker means the validity checker receives
retrieval's raw top-20 output, not a top-5 -- a decision only this
module can make correctly).
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.services.citation_verifier import CitationVerifierResult, verify_citations
from app.services.query_analyzer import QueryAnalysis, analyze_query
from app.services.reasoning_agent import Citation, ReasoningResult, generate_reasoning
from app.services.reranker import rerank
from app.services.retrieval import CaseChunk, retrieve
from app.services.sanitizer import SanitizationResult, SanitizationStatus, sanitize_query
from app.services.validity_checker import ValidityCheckResult, check_validity


class PipelineOutcome(str, Enum):
    ANSWERED = "answered"
    ABSTAIN = "abstain"
    REJECTED_AT_SANITIZATION = "rejected_at_sanitization"
    NO_RETRIEVAL_RESULTS = "no_retrieval_results"


class PipelineDeps(BaseModel):
    """
    Per-stage ablation toggles for the research pipeline. Defaults to the
    full pipeline (all stages enabled). Disabling a stage passes its input
    straight through to the next stage unchanged -- it never substitutes a
    fake or default output in its place.
    """

    enable_reranker: bool = True
    enable_validity_check: bool = True
    enable_citation_verification: bool = True


class PipelineTraceEntry(BaseModel):
    """
    One stage's contribution to the trace log. `data` holds structured
    output from the stage itself (e.g. a stage's own result model, dumped
    via model_dump(mode="json")) -- never a prose summary -- so the
    ablation study can inspect exactly what each stage produced. A
    skipped stage (via an ablation toggle) still appears with
    skipped=True and empty data, so it's visible in the log rather than
    silently absent.
    """

    stage: str
    skipped: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    outcome: PipelineOutcome

    # Populated when outcome is ANSWERED.
    issue: str | None = None
    rule: str | None = None
    application: str | None = None
    conclusion: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    # True only when citation verification actually ran and returned
    # AUTO_ANSWER. Never set True when enable_citation_verification was
    # False -- an ANSWERED result from a skipped verification stage means
    # its citations were never checked, and must not be represented as
    # verified.
    citations_verified: bool = False

    # Populated when outcome is ABSTAIN.
    abstain_reason: str | None = None

    # Populated when outcome is REJECTED_AT_SANITIZATION.
    rejection_reason: str | None = None

    # Populated when outcome is NO_RETRIEVAL_RESULTS.
    message: str | None = None

    trace: list[PipelineTraceEntry] = Field(default_factory=list)


def run_pipeline(
    query: str,
    jurisdiction: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    deps: PipelineDeps | None = None,
) -> PipelineResult:
    """
    Single entry point for the research pipeline. Sequentially runs
    sanitization -> query analysis -> retrieval -> reranking (if enabled)
    -> validity check (if enabled) -> structured reasoning -> citation
    verification (if enabled), short-circuiting immediately at
    sanitization rejection or zero retrieval results without calling any
    further stage.
    """
    if deps is None:
        deps = PipelineDeps()

    trace: list[PipelineTraceEntry] = []

    sanitization_result: SanitizationResult = sanitize_query(query)
    trace.append(PipelineTraceEntry(stage="sanitization", data=sanitization_result.model_dump(mode="json")))

    if sanitization_result.status != SanitizationStatus.PASSED:
        return PipelineResult(
            outcome=PipelineOutcome.REJECTED_AT_SANITIZATION,
            rejection_reason=sanitization_result.reason,
            trace=trace,
        )

    sanitized_query = sanitization_result.query

    query_analysis: QueryAnalysis = analyze_query(sanitized_query)
    trace.append(PipelineTraceEntry(stage="query_analysis", data=query_analysis.model_dump(mode="json")))

    # query_type (FACTUAL/FALSE_PREMISE/EXPLORATORY) never branches the
    # pipeline's shape -- only flagged_premise is threaded through, into
    # generate_reasoning below.
    candidates: list[CaseChunk] = retrieve(
        sanitized_query, jurisdiction=jurisdiction, date_from=date_from, date_to=date_to
    )
    trace.append(
        PipelineTraceEntry(
            stage="retrieval",
            data={
                "result_count": len(candidates),
                "top_scores": [chunk.score for chunk in candidates[:5]],
            },
        )
    )

    if not candidates:
        return PipelineResult(
            outcome=PipelineOutcome.NO_RETRIEVAL_RESULTS,
            message="No case law matching this query was found in the corpus.",
            trace=trace,
        )

    if deps.enable_reranker:
        reranked_chunks = rerank(sanitized_query, candidates)
        trace.append(
            PipelineTraceEntry(
                stage="reranker",
                data={
                    "result_count": len(reranked_chunks),
                    "top_rerank_scores": [chunk.rerank_score for chunk in reranked_chunks[:5]],
                },
            )
        )
    else:
        reranked_chunks = candidates
        trace.append(PipelineTraceEntry(stage="reranker", skipped=True))

    if deps.enable_validity_check:
        validity_result: ValidityCheckResult = check_validity(reranked_chunks)
        valid_chunks = validity_result.valid_chunks
        trace.append(PipelineTraceEntry(stage="validity_check", data=validity_result.model_dump(mode="json")))
    else:
        valid_chunks = reranked_chunks
        trace.append(PipelineTraceEntry(stage="validity_check", skipped=True))

    # Deferred future enhancement, explicitly out of this module's stated
    # scope (sequential wiring + failure handling + trace logging, not a
    # retry/expansion loop): if validity checking or reasoning thin
    # valid_chunks heavily relative to what retrieval/reranking produced,
    # this module does not go back to retrieve() for more candidates. It
    # only reports what happened via the trace; a thinned-out result
    # either reasons over what remains or resolves to insufficient_sources
    # below.

    reasoning_result: ReasoningResult = generate_reasoning(
        sanitized_query, valid_chunks, flagged_premise=query_analysis.flagged_premise
    )
    trace.append(PipelineTraceEntry(stage="reasoning", data=reasoning_result.model_dump(mode="json")))

    if deps.enable_citation_verification:
        verifier_result: CitationVerifierResult = verify_citations(reasoning_result, valid_chunks)
        trace.append(
            PipelineTraceEntry(stage="citation_verification", data=verifier_result.model_dump(mode="json"))
        )

        if verifier_result.outcome == "ABSTAIN":
            return PipelineResult(
                outcome=PipelineOutcome.ABSTAIN,
                abstain_reason=verifier_result.abstain_reason,
                trace=trace,
            )

        return PipelineResult(
            outcome=PipelineOutcome.ANSWERED,
            issue=reasoning_result.issue,
            rule=reasoning_result.rule,
            application=reasoning_result.application,
            conclusion=reasoning_result.conclusion,
            citations=reasoning_result.citations,
            citations_verified=True,
            trace=trace,
        )

    trace.append(PipelineTraceEntry(stage="citation_verification", skipped=True))

    # Citation verification was skipped, so its own insufficient_sources
    # short-circuit (which would otherwise turn this into ABSTAIN) never
    # ran. The Reasoning Agent's insufficient_sources is itself a
    # no-answer signal, not something to paper over as ANSWERED with
    # empty IRAC fields just because the ablation toggle disabled the
    # next stage -- see docs/DECISIONS.md for this one.
    if reasoning_result.insufficient_sources:
        return PipelineResult(
            outcome=PipelineOutcome.ABSTAIN,
            abstain_reason=reasoning_result.insufficient_sources_reason,
            trace=trace,
        )

    return PipelineResult(
        outcome=PipelineOutcome.ANSWERED,
        issue=reasoning_result.issue,
        rule=reasoning_result.rule,
        application=reasoning_result.application,
        conclusion=reasoning_result.conclusion,
        citations=reasoning_result.citations,
        citations_verified=False,
        trace=trace,
    )
