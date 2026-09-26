# NOTE(scope): this agent does not re-verify citations against source text
# (Module 6's exclusive job, per the core invariant that only the Citation
# Verifier Agent marks a citation "verified") and does not decide to abstain
# when insufficient_sources is True -- it only reports that state. Requesting
# more candidates from retrieval when sources are thin is also out of scope,
# deferred to Module 7's orchestration layer, which doesn't exist yet.
"""
Module 5 -- Structured Reasoning Agent.

Given a query, Module 2's false-premise flag (if any), and Module 4's
validated case chunks, produces a strict IRAC-formatted answer where
every claim traces to a specific validated source via an inline [N]
citation marker. Trusts valid_chunks as already-verified ground truth.

Citation markers are assigned deterministically by this module, not the
LLM: valid_chunks are numbered 1..N in the prompt, the LLM is only asked
to write text using [N] markers referring to that fixed numbering, and
the final `citations` list is built here from the real chunk metadata
(case_id/case_name/citation/chunk_id) for whichever markers actually
appear in the output. This avoids ever trusting the LLM to reproduce a
UUID or citation string from memory.
"""

import re

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.core.config import settings
from app.services.retrieval import CaseChunk


class Citation(BaseModel):
    marker: int
    case_id: str
    case_name: str
    citation: str
    chunk_id: str


class ReasoningResult(BaseModel):
    insufficient_sources: bool
    insufficient_sources_reason: str | None = None
    issue: str | None = None
    rule: str | None = None
    application: str | None = None
    conclusion: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    addressed_false_premise: bool = False


class ReasoningDraft(BaseModel):
    """Raw structured output from the LLM, before deterministic citation
    extraction. Never exposes case_id/chunk_id fields for the model to
    fill in -- those are attached afterward from the real source list."""

    insufficient_sources: bool
    insufficient_sources_reason: str | None = Field(
        default=None,
        description="Populated only when insufficient_sources is True, explaining what's missing.",
    )
    issue: str | None = None
    rule: str | None = None
    application: str | None = None
    conclusion: str | None = None
    addressed_false_premise: bool = Field(
        default=False,
        description="True only when a false premise was flagged and this output addresses it "
        "directly instead of producing a standard IRAC analysis.",
    )


SYSTEM_PROMPT = """You are the Structured Reasoning Agent for a legal research assistant. \
You will be given a legal research query, optionally a flagged false premise, and a \
numbered list of validated case-law source excerpts. Every source is already-verified \
ground truth -- you do not verify citations yourself, and you must never introduce a \
case, holding, or fact that is not supported by one of the numbered sources.

Rules:
1. If a false premise is flagged, do not produce a standard IRAC breakdown. Instead, \
directly explain what is actually true and why, grounded in the numbered sources, \
writing that explanation into the `application` field (leave `issue`/`rule`/`conclusion` \
null in this case), and set addressed_false_premise=True.
2. If the numbered sources are empty, or they don't actually contain enough information \
to answer the query (or to address the flagged false premise), set \
insufficient_sources=True, give a specific reason in insufficient_sources_reason, and \
leave issue/rule/application/conclusion null. Do not fabricate an answer to avoid this \
outcome.
3. Otherwise, produce a standard IRAC answer across issue/rule/application/conclusion. \
Every factual or legal claim in rule, application, and (where it relies on a claim) \
conclusion must be tagged with an inline citation marker in the exact form [N], where N \
is the number of the source it relies on from the numbered list. A single claim may cite \
multiple sources (e.g. "...[1][3]"); a single source may support multiple claims -- don't \
force a one-to-one mapping. Never invent a marker number that isn't in the numbered list.
4. Only set addressed_false_premise=True when a false premise was actually flagged and \
addressed per rule 1; otherwise leave it False."""


reasoning_agent: Agent[None, ReasoningDraft] = Agent(
    f"openrouter:{settings.structured_reasoning_model}",
    output_type=ReasoningDraft,
    system_prompt=SYSTEM_PROMPT,
    defer_model_check=True,
)


_MARKER_PATTERN = re.compile(r"\[(\d+)\]")
_MARKER_PATTERN_WITH_LEADING_SPACE = re.compile(r"\s?\[(\d+)\]")


def _format_sources(valid_chunks: list[CaseChunk]) -> str:
    return "\n\n".join(
        f"SOURCE [{index}]: {chunk.case_name} ({chunk.citation})\n{chunk.chunk_text}"
        for index, chunk in enumerate(valid_chunks, start=1)
    )


def _build_prompt(query: str, valid_chunks: list[CaseChunk], flagged_premise: str | None) -> str:
    parts = [f"QUERY: {query}"]
    if flagged_premise:
        parts.append(f"FLAGGED FALSE PREMISE: {flagged_premise}")
    parts.append(
        "NUMBERED SOURCES:\n" + _format_sources(valid_chunks) if valid_chunks else "NUMBERED SOURCES: (none provided)"
    )
    return "\n\n".join(parts)


def _extract_citations(text_fields: list[str | None], valid_chunks: list[CaseChunk]) -> list[Citation]:
    """
    Scans every IRAC text field for [N] markers and builds one Citation per
    distinct marker actually used, sourced from the real valid_chunks data
    rather than anything the LLM output. Out-of-range marker numbers (a
    hallucinated reference beyond the numbered source list) are silently
    dropped rather than raising, since the input chunks are the only
    trustworthy source of truth here.
    """
    markers_used: set[int] = set()
    for text in text_fields:
        if not text:
            continue
        for match in _MARKER_PATTERN.finditer(text):
            marker = int(match.group(1))
            if 1 <= marker <= len(valid_chunks):
                markers_used.add(marker)

    return [
        Citation(
            marker=marker,
            case_id=valid_chunks[marker - 1].case_id,
            case_name=valid_chunks[marker - 1].case_name,
            citation=valid_chunks[marker - 1].citation,
            chunk_id=valid_chunks[marker - 1].id,
        )
        for marker in sorted(markers_used)
    ]


def _strip_invalid_markers(text: str | None, valid_chunks: list[CaseChunk]) -> str | None:
    """
    Removes any [N] marker (plus one leading space, if present) where N
    falls outside 1..len(valid_chunks) -- a hallucinated reference that
    _extract_citations already excluded from the citations list, but
    which would otherwise be left dangling in the displayed text with
    nothing backing it. In-range markers are left untouched.
    """
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        marker = int(match.group(1))
        return match.group(0) if 1 <= marker <= len(valid_chunks) else ""

    return _MARKER_PATTERN_WITH_LEADING_SPACE.sub(_replace, text)


def generate_reasoning(
    query: str,
    valid_chunks: list[CaseChunk],
    flagged_premise: str | None = None,
) -> ReasoningResult:
    """
    Single entry point for the Structured Reasoning Agent.

    Short-circuits deterministically (no LLM call) when valid_chunks is
    empty, since there is nothing for a model to reason over. Otherwise
    delegates the insufficient-sources judgment call (chunks present but
    not actually enough to answer) to the LLM, per rule 2 of the prompt.
    """
    if not valid_chunks:
        return ReasoningResult(
            insufficient_sources=True,
            insufficient_sources_reason="No validated source chunks were provided.",
        )

    prompt = _build_prompt(query, valid_chunks, flagged_premise)
    draft = reasoning_agent.run_sync(prompt).output

    if draft.insufficient_sources:
        return ReasoningResult(
            insufficient_sources=True,
            insufficient_sources_reason=draft.insufficient_sources_reason,
        )

    citations = _extract_citations(
        [draft.issue, draft.rule, draft.application, draft.conclusion],
        valid_chunks,
    )

    return ReasoningResult(
        insufficient_sources=False,
        issue=_strip_invalid_markers(draft.issue, valid_chunks),
        rule=_strip_invalid_markers(draft.rule, valid_chunks),
        application=_strip_invalid_markers(draft.application, valid_chunks),
        conclusion=_strip_invalid_markers(draft.conclusion, valid_chunks),
        citations=citations,
        addressed_false_premise=bool(flagged_premise) and draft.addressed_false_premise,
    )
