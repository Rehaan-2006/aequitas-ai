# NOTE(scope): this module never fetches research_threads (or any other
# table) for its own inputs -- query/irac_result/verified_chunks always
# arrive as already-verified parameters from the caller. The only DB read
# here is the Template Matcher's lookup of the template row itself by
# template_id, which is data this module owns (structure_schema), not
# research data. Persisting the resulting DraftResult into legal_drafts,
# approval/rejection of a draft, credits, and any API route are all
# Module 9 (Backend API) concerns, not this one.
#
# PDF/DOCX rendering is also out of scope -- this module only ever
# produces structured content_json. Rendering a drafted document to a
# downloadable file is deferred to Module 9's export endpoint, which is
# also where the action-gate approval check must live before any render
# or download is allowed to happen.
"""
Module 8 -- Drafting Agent.

Given a template and already-verified research (an IRAC ReasoningResult
plus the exact CaseChunk list it was reasoned over), drafts one section
of the template at a time, then re-runs the assembled draft through
Module 6's citation verifier before returning it with approval_status
always 'pending_review' -- this module never marks a draft 'approved',
that is a separate, explicit future action gated behind user approval.

Template selection is not an LLM decision: draft_document takes an
explicit template_id and never infers which template to use (see
docs/DECISIONS.md). Each required section is drafted by its own agent
call, scoped to the subset of the IRAC research relevant to that section,
so one section's failure or hallucination risk never contaminates
another's context (see docs/DECISIONS.md).
"""

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.core.config import settings
from app.db.supabase_client import get_supabase_client
from app.services.citation_verifier import verify_citations
from app.services.reasoning_agent import Citation, ReasoningResult
from app.services.retrieval import CaseChunk


class TemplateSection(BaseModel):
    name: str
    description: str
    required: bool = True


class DocumentTemplate(BaseModel):
    id: str
    title: str
    sections: list[TemplateSection]


class DraftResult(BaseModel):
    content_json: dict[str, str]
    verification_status: Literal["verified", "failed"]
    approval_status: Literal["pending_review"] = "pending_review"
    failure_reason: str | None = None


class _SectionDraft(BaseModel):
    """Raw per-section output from the LLM. Never carries case_id/chunk_id
    -- citations are resolved afterward from verified_chunks metadata only,
    the same real-metadata-only pattern Module 5 and Module 6 both use."""

    content: str = Field(description="The drafted prose for this section only -- no heading.")


SYSTEM_PROMPT = """You are the Section-by-Section Drafter for a legal document drafting \
system. You will be given a document's query/matter, the name and purpose of exactly one \
section to draft, relevant excerpts from already-verified legal research, and (when \
applicable) a numbered list of source excerpts you may cite.

Rules:
1. Draft ONLY the requested section's content. Do not restate the section name as a \
heading, and do not draft any other section.
2. Write in professional legal drafting style appropriate to a filed legal document.
3. If numbered sources are provided, you may cite one by writing the exact marker [N] \
immediately after the claim it supports. Never invent a marker number that isn't in the \
numbered list, and never state a holding, fact, or case name that isn't grounded in one of \
the numbered sources.
4. If no numbered sources are provided for this section, write it without any case \
citation -- do not fabricate one.
5. For administrative/case-caption details that are not knowable from the research \
provided (e.g. court name, party names, docket number, date), use a clearly bracketed \
placeholder such as [COURT NAME] or [PLAINTIFF] rather than inventing a real-sounding value.
6. Always produce real, substantive content for the section -- never leave it blank and \
never respond with only a placeholder like "TBD" or a restatement of the section's \
description."""


section_drafter_agent: Agent[None, _SectionDraft] = Agent(
    f"openrouter:{settings.section_drafter_model}",
    output_type=_SectionDraft,
    system_prompt=SYSTEM_PROMPT,
    defer_model_check=True,
)


_MARKER_PATTERN = re.compile(r"\[(\d+)\]")
_MARKER_PATTERN_WITH_LEADING_SPACE = re.compile(r"\s?\[(\d+)\]")

_IRAC_FIELDS = ["issue", "rule", "application", "conclusion"]

# Keyword-based mapping from a section's name to the subset of the IRAC
# research actually relevant to it, so each section's LLM call only sees
# the research (and, by extension via _markers_in_fields below, only the
# numbered sources) pertinent to that section rather than the whole
# research result every time. First matching pattern wins; a section name
# that matches nothing falls back to the full IRAC result.
_SECTION_FIELD_KEYWORDS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"caption", re.I), []),
    (re.compile(r"\bheading\b", re.I), []),
    (re.compile(r"\bheader\b", re.I), []),
    # Statement of Facts recites the case's own alleged/underlying facts --
    # it never cites case law to support them. Mapping it to a research
    # field (e.g. "application") invites the drafter to cite a legal-rule
    # source in support of a fact-specific claim, which the source can
    # never actually verify and which live-integration testing showed
    # citation_verifier correctly flags (source affirms a general rule;
    # claim asserts a specific unrelated fact -- a genuine polarity/support
    # mismatch, not a false positive). Treated like Caption/Heading: no
    # research shown, no citation invited.
    (re.compile(r"statement of facts|factual background", re.I), []),
    (re.compile(r"question presented", re.I), ["issue"]),
    (re.compile(r"introduction", re.I), ["issue"]),
    (re.compile(r"brief answer", re.I), ["conclusion"]),
    (re.compile(r"legal basis|legal grounds", re.I), ["rule"]),
    (re.compile(r"argument|discussion|analysis", re.I), ["rule", "application"]),
    (re.compile(r"prayer for relief|relief requested", re.I), ["conclusion"]),
    (re.compile(r"consequences|non-compliance", re.I), ["conclusion"]),
    (re.compile(r"demand", re.I), ["conclusion"]),
    (re.compile(r"conclusion", re.I), ["conclusion"]),
]


def _relevant_irac_fields(section_name: str) -> list[str]:
    for pattern, fields in _SECTION_FIELD_KEYWORDS:
        if pattern.search(section_name):
            return fields
    return list(_IRAC_FIELDS)


def _load_template(client, template_id: uuid.UUID) -> DocumentTemplate:
    response = (
        client.table("document_templates")
        .select("id, title, structure_schema")
        .eq("id", str(template_id))
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise ValueError(f"No document_templates row found for template_id={template_id}")

    row = rows[0]
    sections = [TemplateSection(**section) for section in row["structure_schema"]["sections"]]
    return DocumentTemplate(id=row["id"], title=row["title"], sections=sections)


def _format_sources(verified_chunks: list[CaseChunk], markers: list[int] | None = None) -> str:
    indices = markers if markers is not None else list(range(1, len(verified_chunks) + 1))
    parts = []
    for index in indices:
        chunk = verified_chunks[index - 1]
        parts.append(f"SOURCE [{index}]: {chunk.case_name} ({chunk.citation})\n{chunk.chunk_text}")
    return "\n\n".join(parts)


def _markers_in_fields(irac_result: ReasoningResult, fields: list[str]) -> list[int]:
    markers: set[int] = set()
    for field in fields:
        text = getattr(irac_result, field)
        if text:
            markers.update(int(m) for m in _MARKER_PATTERN.findall(text))
    return sorted(markers)


def _build_section_prompt(
    query: str,
    section: TemplateSection,
    irac_result: ReasoningResult,
    verified_chunks: list[CaseChunk],
) -> str:
    fields = _relevant_irac_fields(section.name)

    parts = [
        f"DOCUMENT MATTER: {query}",
        f"SECTION TO DRAFT: {section.name}",
        f"SECTION PURPOSE: {section.description}",
    ]

    field_text = "\n\n".join(f"{field.upper()}: {getattr(irac_result, field)}" for field in fields if getattr(irac_result, field))
    if field_text:
        parts.append(f"RELEVANT RESEARCH:\n{field_text}")

    if not fields:
        parts.append("NUMBERED SOURCES: (none relevant to this section -- do not cite any case.)")
    else:
        markers = _markers_in_fields(irac_result, fields)
        valid_markers = [m for m in markers if 1 <= m <= len(verified_chunks)]
        scoped_markers = valid_markers or list(range(1, len(verified_chunks) + 1))
        if scoped_markers:
            parts.append(f"NUMBERED SOURCES (cite only these, using [N]):\n{_format_sources(verified_chunks, scoped_markers)}")
        else:
            parts.append("NUMBERED SOURCES: (none available -- do not cite any case.)")

    return "\n\n".join(parts)


def _strip_invalid_markers(text: str, verified_chunks: list[CaseChunk]) -> str:
    def _replace(match: re.Match) -> str:
        marker = int(match.group(1))
        return match.group(0) if 1 <= marker <= len(verified_chunks) else ""

    return _MARKER_PATTERN_WITH_LEADING_SPACE.sub(_replace, text)


def _extract_citations(content_json: dict[str, str], verified_chunks: list[CaseChunk]) -> list[Citation]:
    markers_used: set[int] = set()
    for text in content_json.values():
        for match in _MARKER_PATTERN.finditer(text):
            marker = int(match.group(1))
            if 1 <= marker <= len(verified_chunks):
                markers_used.add(marker)

    return [
        Citation(
            marker=marker,
            case_id=verified_chunks[marker - 1].case_id,
            case_name=verified_chunks[marker - 1].case_name,
            citation=verified_chunks[marker - 1].citation,
            chunk_id=verified_chunks[marker - 1].id,
        )
        for marker in sorted(markers_used)
    ]


def draft_document(
    query: str,
    irac_result: ReasoningResult,
    verified_chunks: list[CaseChunk],
    template_id: uuid.UUID,
) -> DraftResult:
    """
    Single entry point for the Drafting Agent.

    Template Matcher -> Section-by-Section Drafter (one agent call per
    required section) -> Citation Injector (markers only ever resolve
    against verified_chunks, never a fresh retrieve() call) -> re-run
    through Module 6's verify_citations() unmodified. Every required
    section is checked non-empty before returning; an empty one is
    surfaced as verification_status='failed', never silently dropped.
    """
    if irac_result.insufficient_sources:
        return DraftResult(
            content_json={},
            verification_status="failed",
            failure_reason="Cannot draft from a research result that reported insufficient sources.",
        )

    client = get_supabase_client()
    template = _load_template(client, template_id)

    required_sections = [section for section in template.sections if section.required]

    content_json: dict[str, str] = {}
    empty_sections: list[str] = []

    for section in required_sections:
        prompt = _build_section_prompt(query, section, irac_result, verified_chunks)
        draft = section_drafter_agent.run_sync(prompt).output
        content = _strip_invalid_markers(draft.content, verified_chunks).strip()
        content_json[section.name] = content
        if not content:
            empty_sections.append(section.name)

    if empty_sections:
        return DraftResult(
            content_json=content_json,
            verification_status="failed",
            failure_reason=(
                "Drafter produced empty output for required section(s): " + ", ".join(empty_sections)
            ),
        )

    citations = _extract_citations(content_json, verified_chunks)
    assembled_for_verification = ReasoningResult(
        insufficient_sources=False,
        rule="\n\n".join(content_json[section.name] for section in required_sections),
        citations=citations,
    )

    verifier_result = verify_citations(assembled_for_verification, verified_chunks)

    if verifier_result.outcome == "AUTO_ANSWER":
        return DraftResult(content_json=content_json, verification_status="verified")

    return DraftResult(
        content_json=content_json,
        verification_status="failed",
        failure_reason=verifier_result.abstain_reason,
    )
