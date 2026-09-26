# CORE INVARIANT: this is the ONLY module in the entire pipeline permitted
# to mark a citation "VERIFIED". Retrieval, reranking, validity checking,
# and structured reasoning never independently verify a claim against
# source text, re-implement this logic, or shortcut it -- they only pass
# candidates through or report what they did. Nothing upstream of this
# module is trusted to have already done this check.
"""
Module 6 -- Citation Verifier Agent.

Given Module 5's IRAC reasoning output and the same valid_chunks it
reasoned over, independently judges whether each cited source's actual
text supports the specific claim it's attached to -- VERIFIED (clearly
supports), CONTRADICTED (actively disagrees), or UNVERIFIED (neither).

Module 5's citation markers already resolve deterministically to real
case_chunks rows by construction (numbered 1..N against valid_chunks,
with any out-of-range marker already stripped before this module ever
sees the text) -- no LLM-invented citation string reaches this module.
The synthetic-citation-identifier / injected-citation risk flagged in
the project status doc applies to a future Drafting Agent module
(verifying externally-referenced citations against the corpus), not to
this one, and nothing here is built to address it.

Only two outcomes apply to this research-flow module: AUTO_ANSWER and
ABSTAIN. ACTION_GATE is specific to drafted-document export (a future
Drafting Agent module) and is out of scope here. This module never
rewrites, edits, or "fixes" Module 5's text, and never re-runs retrieval
or requests more sources on a failure -- it only judges and reports;
deciding what to do about a failure is Module 7's orchestration job.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.core.config import settings
from app.services.reasoning_agent import ReasoningResult
from app.services.retrieval import CaseChunk

Verdict = Literal["VERIFIED", "UNVERIFIED", "CONTRADICTED"]
Polarity = Literal["AFFIRMATIVE", "NEGATIVE"]


class CitationVerdict(BaseModel):
    marker: int
    case_id: str
    case_name: str
    verdict: Verdict
    explanation: str


class CitationVerifierResult(BaseModel):
    outcome: Literal["AUTO_ANSWER", "ABSTAIN"]
    verdicts: list[CitationVerdict] = Field(default_factory=list)
    abstain_reason: str | None = None


class _VerdictDraft(BaseModel):
    """Raw per-marker judgment from the LLM. Never carries case_id/case_name
    -- those are attached afterward from reasoning_result.citations, the
    same real-metadata-only pattern Module 5 uses for its Citation list.

    isolated_claim/claim_polarity/source_polarity exist because trusting the
    model to both extract a claim's polarity AND compare it against the
    source's polarity in one holistic pass proved unreliable in adversarial
    testing (a verdict of VERIFIED accompanied by a plainly wrong "polarity
    matches" explanation on a claim that had actually been polarity-reversed).
    Instead of trusting the model's own claim that things match, code in
    verify_citations independently compares claim_polarity against
    source_polarity and overrides the verdict on a mismatch -- these fields
    are load-bearing, not decorative."""

    marker: int
    isolated_claim: str = Field(
        description=(
            "The specific clause or sentence tied to this marker, extracted fresh and in "
            "isolation from the rest of the paragraph -- not a summary of the whole passage."
        )
    )
    claim_polarity: Polarity = Field(
        description=(
            "Does the isolated claim assert something IS the case / IS permitted, required, or "
            "does apply (AFFIRMATIVE), or that it is NOT / is prohibited / not required / does "
            "not apply (NEGATIVE)?"
        )
    )
    source_polarity: Polarity = Field(
        description="The same AFFIRMATIVE/NEGATIVE judgment applied to what SOURCE [N] says about that same specific point."
    )
    verdict: Verdict
    explanation: str = Field(
        description="Brief, grounded in the specific quoted source text -- not a general impression."
    )


class _CitationVerifierDraft(BaseModel):
    verdicts: list[_VerdictDraft]


SYSTEM_PROMPT = """You are the Citation Verifier Agent for a legal research assistant. \
You will be given a structured IRAC legal answer (with inline [N] citation markers left \
intact in the text) and a numbered list of the exact source excerpts each marker refers \
to. You are the ONLY component in this system permitted to mark a citation verified -- \
treat that responsibility seriously and never rubber-stamp.

For each distinct marker number that appears in the IRAC text, do the following:
1. isolated_claim: identify, from context, the specific clause or sentence that marker is \
attached to, and write it out fresh, in isolation. Do not summarize or paraphrase the whole \
passage -- pin down the exact claim next to that marker and nothing else.
2. claim_polarity: judge whether that isolated claim, on its own, asserts that something IS \
the case, IS permitted, IS required, or DOES apply (AFFIRMATIVE) -- or that something is NOT \
the case, is prohibited, is NOT required, or does NOT apply (NEGATIVE). Every legal claim has \
one of these two polarities; pick exactly one.
3. source_polarity: read SOURCE [N] (the excerpt with the matching number) and make the same \
AFFIRMATIVE/NEGATIVE judgment about what the source's own operative content says on that same \
specific point, independent of what the claim said.
4. verdict: judge whether the source excerpt clearly and specifically supports the isolated \
claim:
   - VERIFIED: the source excerpt clearly and specifically supports the claim.
   - CONTRADICTED: the source excerpt actively says something that disagrees with the claim \
(e.g. states the opposite holding, outcome, standard, or polarity).
   - UNVERIFIED: neither of the above -- the excerpt is ambiguous, off-point, or doesn't \
overlap enough with the claim to confirm it either way.
5. explanation: ground it in the actual text of the source excerpt, not a general impression \
of the case. Keep it brief but specific (quote or closely paraphrase the relevant part of the \
source).

Note that claim_polarity and source_polarity are independent, separately-judged fields -- \
do not simply copy claim_polarity into source_polarity because the surrounding text looks \
similar. A claim can share most of its wording and supporting detail with the source and \
still have the opposite polarity on the one specific point that matters (e.g. the claim says \
something is prohibited while the source says it is permitted); read each source excerpt on \
its own terms for this judgment.

If the same claim cites multiple markers (e.g. "...[1][3]"), still judge each \
marker/source pair independently and return a separate verdict for each -- do not merge \
them into one combined verdict.

Return exactly one verdict per distinct marker that appears in the text. Never invent a \
marker number that isn't in the numbered source list."""


citation_verifier_agent: Agent[None, _CitationVerifierDraft] = Agent(
    f"openrouter:{settings.citation_verifier_model}",
    output_type=_CitationVerifierDraft,
    system_prompt=SYSTEM_PROMPT,
    defer_model_check=True,
)


_MARKER_PATTERN = re.compile(r"\[(\d+)\]")
_MARKER_GROUP_PATTERN = re.compile(r"(?:\[\d+\])+")


def _extract_marker_groups(text_fields: list[str | None]) -> list[list[int]]:
    """
    Groups markers that appear back-to-back with no separating text (e.g.
    "...[1][3]") into one claim group, since that's the exact convention
    Module 5's prompt and tests already use for "a single claim relying
    on two sources at once." This is a deterministic scan for adjacent
    bracket sequences, not sentence-splitting -- it never inspects
    surrounding prose, so it isn't tripped up by periods in citations or
    abbreviations like "U.S." or "F. Supp. 2d".
    """
    groups: list[list[int]] = []
    for text in text_fields:
        if not text:
            continue
        for group_match in _MARKER_GROUP_PATTERN.finditer(text):
            groups.append([int(m) for m in _MARKER_PATTERN.findall(group_match.group(0))])
    return groups


def _format_reasoning_text(reasoning_result: ReasoningResult) -> str:
    labeled_fields = [
        ("ISSUE", reasoning_result.issue),
        ("RULE", reasoning_result.rule),
        ("APPLICATION", reasoning_result.application),
        ("CONCLUSION", reasoning_result.conclusion),
    ]
    return "\n\n".join(f"{label}: {text}" for label, text in labeled_fields if text)


def _format_cited_sources(citation_markers: list[int], valid_chunks: list[CaseChunk]) -> str:
    parts = []
    for marker in sorted(citation_markers):
        chunk = valid_chunks[marker - 1]
        parts.append(f"SOURCE [{marker}]: {chunk.case_name} ({chunk.citation})\n{chunk.chunk_text}")
    return "\n\n".join(parts)


def _build_prompt(reasoning_result: ReasoningResult, valid_chunks: list[CaseChunk], citation_markers: list[int]) -> str:
    return (
        "REASONING OUTPUT:\n"
        f"{_format_reasoning_text(reasoning_result)}\n\n"
        "NUMBERED SOURCES:\n"
        f"{_format_cited_sources(citation_markers, valid_chunks)}"
    )


def verify_citations(reasoning_result: ReasoningResult, valid_chunks: list[CaseChunk]) -> CitationVerifierResult:
    """
    Single entry point for the Citation Verifier Agent.

    Short-circuits deterministically (no LLM call) in two cases: Module 5
    already reported insufficient_sources, or a non-abstaining answer
    somehow carries zero citations (treated as suspicious, not benign --
    a real IRAC answer should always trace to at least one source).
    Otherwise makes exactly one LLM call covering every marker across all
    four IRAC fields together.
    """
    if reasoning_result.insufficient_sources:
        return CitationVerifierResult(
            outcome="ABSTAIN",
            abstain_reason="Module 5 already reported insufficient sources to answer this query.",
        )

    if not reasoning_result.citations:
        return CitationVerifierResult(
            outcome="ABSTAIN",
            abstain_reason=(
                "The reasoning output did not report insufficient sources but contains zero "
                "supporting citations -- treated as suspicious, not benign."
            ),
        )

    citation_by_marker = {citation.marker: citation for citation in reasoning_result.citations}
    prompt = _build_prompt(reasoning_result, valid_chunks, list(citation_by_marker))
    draft = citation_verifier_agent.run_sync(prompt).output

    draft_by_marker = {v.marker: v for v in draft.verdicts if v.marker in citation_by_marker}

    verdicts: list[CitationVerdict] = []
    for marker, citation in sorted(citation_by_marker.items()):
        draft_verdict = draft_by_marker.get(marker)
        if draft_verdict is None:
            verdicts.append(
                CitationVerdict(
                    marker=marker,
                    case_id=citation.case_id,
                    case_name=citation.case_name,
                    verdict="UNVERIFIED",
                    explanation="The verifier model returned no judgment for this marker.",
                )
            )
        else:
            verdict = draft_verdict.verdict
            explanation = draft_verdict.explanation
            if draft_verdict.claim_polarity != draft_verdict.source_polarity:
                # Code-enforced override, not a prompt instruction: adversarial testing showed
                # the model's own free-text claim that "directionality matches" can be wrong on
                # a polarity-reversed claim, even after being explicitly asked to compare. We
                # never trust that self-assessment -- we independently compare the two discrete
                # polarity fields ourselves and force CONTRADICTED on any mismatch, regardless of
                # whatever verdict the model separately assigned.
                verdict = "CONTRADICTED"
                explanation = (
                    f"Overridden to CONTRADICTED: claim polarity ({draft_verdict.claim_polarity}) does not "
                    f"match source polarity ({draft_verdict.source_polarity}). Model's own explanation: "
                    f"{draft_verdict.explanation}"
                )
            verdicts.append(
                CitationVerdict(
                    marker=marker,
                    case_id=citation.case_id,
                    case_name=citation.case_name,
                    verdict=verdict,
                    explanation=explanation,
                )
            )

    verdict_by_marker = {v.marker: v.verdict for v in verdicts}
    claim_groups = _extract_marker_groups(
        [reasoning_result.issue, reasoning_result.rule, reasoning_result.application, reasoning_result.conclusion]
    ) or [[marker] for marker in citation_by_marker]

    failing_claims: list[str] = []
    for group in claim_groups:
        group_verdicts = [verdict_by_marker[m] for m in group if m in verdict_by_marker]
        if not any(v == "VERIFIED" for v in group_verdicts):
            marker_label = "".join(f"[{m}]" for m in group)
            detail = "; ".join(f"marker {m}: {verdict_by_marker.get(m, 'UNVERIFIED')}" for m in group)
            failing_claims.append(f"{marker_label} ({detail})")

    if failing_claims:
        return CitationVerifierResult(
            outcome="ABSTAIN",
            verdicts=verdicts,
            abstain_reason="The following claim(s) failed citation verification: " + "; ".join(failing_claims),
        )

    return CitationVerifierResult(outcome="AUTO_ANSWER", verdicts=verdicts)
