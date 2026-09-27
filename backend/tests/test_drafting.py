"""
Module 8 -- Drafting Agent tests.

Mocked suite (default CI, no API cost): swaps section_drafter_agent's
model for a pydantic_ai FunctionModel that inspects each call's own
prompt text to return content differentiated by which section is being
drafted -- a single TestModel(custom_output_args=...) can't do this,
since draft_document makes one distinct LLM call per required section,
not one call for the whole document. The Template Matcher's Supabase
read uses the same FakeSupabaseClient pattern as
tests/test_validity_checker.py. citation_verifier_agent is exercised for
real (not stubbed out) via its own TestModel override, same pattern as
tests/test_citation_verifier.py, so these tests prove the two modules
compose correctly rather than mocking the seam between them away.

Integration tests at the bottom require live Openrouter + Supabase
credentials and run one real seeded template end-to-end against a real
research result; excluded from default CI (`pytest -m "not integration"`).
"""

import uuid

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from app.services.citation_verifier import citation_verifier_agent
from app.services.drafting import DraftResult, draft_document, section_drafter_agent
from app.services.reasoning_agent import Citation, ReasoningResult
from app.services.retrieval import CaseChunk


# =====================
# Fakes / fixtures
# =====================


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

    def execute(self):
        return _FakeResult(self._data)


class FakeSupabaseClient:
    """Stands in for a real Supabase client: table() calls are resolved
    by name against canned response data, ignoring filter params -- same
    convention as tests/test_validity_checker.py."""

    def __init__(self, table_data: dict | None = None):
        self._table_data = table_data or {}

    def table(self, name):
        return _FakeQuery(self._table_data.get(name, []))


MOTION_TO_DISMISS_SECTIONS = [
    {"name": "Caption", "description": "Court, parties, case number, and the motion title.", "required": True},
    {"name": "Introduction", "description": "Brief statement of the motion and relief sought.", "required": True},
    {"name": "Statement of Facts", "description": "The well-pleaded facts as alleged.", "required": True},
    {"name": "Argument", "description": "Legal standard and argument for dismissal.", "required": True},
    {"name": "Conclusion", "description": "Concise summary of why dismissal is warranted.", "required": True},
    {"name": "Prayer for Relief", "description": "The specific relief requested.", "required": True},
]

DEMAND_LETTER_SECTIONS = [
    {"name": "Header", "description": "Date, sender, and recipient information.", "required": True},
    {"name": "Introduction", "description": "Identify the claim and parties.", "required": True},
    {"name": "Statement of Facts", "description": "Facts giving rise to the claim.", "required": True},
    {"name": "Legal Basis", "description": "Legal grounds supporting the claim.", "required": True},
    {"name": "Demand", "description": "The specific demand being made.", "required": True},
    {"name": "Consequences of Non-Compliance", "description": "Intent to pursue further action.", "required": True},
]

LEGAL_MEMO_SECTIONS = [
    {"name": "Heading", "description": "To/From/Re/Date block.", "required": True},
    {"name": "Question Presented", "description": "The precise legal question.", "required": True},
    {"name": "Brief Answer", "description": "Concise direct answer.", "required": True},
    {"name": "Statement of Facts", "description": "Relevant facts.", "required": True},
    {"name": "Discussion", "description": "Full legal analysis.", "required": True},
    {"name": "Conclusion", "description": "Restates the answer.", "required": True},
]


def _install_fake_template(monkeypatch, sections, title="Test Template", template_id=None):
    template_id = template_id or uuid.uuid4()
    client = FakeSupabaseClient(
        table_data={
            "document_templates": [
                {"id": str(template_id), "title": title, "structure_schema": {"sections": sections}}
            ]
        }
    )
    monkeypatch.setattr("app.services.drafting.get_supabase_client", lambda: client)
    return template_id


def _make_chunk(case_id: str, case_name: str, citation: str, chunk_text: str = "excerpt") -> CaseChunk:
    return CaseChunk(
        id=f"chunk-{case_id}",
        case_id=case_id,
        chunk_index=0,
        chunk_text=chunk_text,
        case_name=case_name,
        citation=citation,
    )


def _make_irac_result(**overrides) -> ReasoningResult:
    defaults = dict(
        insufficient_sources=False,
        issue="Whether the standard applies here.",
        rule="The governing rule is X [1].",
        application="Applying the rule, the facts satisfy X [1].",
        conclusion="Therefore the standard is met [1].",
        citations=[
            Citation(marker=1, case_id="case-a", case_name="Terry v. Ohio", citation="392 U.S. 1", chunk_id="chunk-case-a")
        ],
    )
    defaults.update(overrides)
    return ReasoningResult(**defaults)


def _section_content_function(content_by_section: dict[str, str], default: str = "Drafted content for this section [1]."):
    """Builds a FunctionModel callable that returns section-differentiated
    content by inspecting each call's own prompt text for which section is
    currently being drafted."""

    def func(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        prompt_text = messages[-1].parts[-1].content
        content = default
        for section_name, section_content in content_by_section.items():
            if f"SECTION TO DRAFT: {section_name}" in prompt_text:
                content = section_content
                break
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args={"content": content})])

    return func


def _verified_citation_verifier() -> TestModel:
    """Standard all-VERIFIED citation_verifier_agent override, so
    draft_document's re-verification step resolves to AUTO_ANSWER."""
    return TestModel(
        custom_output_args={
            "verdicts": [
                {
                    "marker": 1,
                    "isolated_claim": "The governing rule is X.",
                    "claim_polarity": "AFFIRMATIVE",
                    "source_polarity": "AFFIRMATIVE",
                    "verdict": "VERIFIED",
                    "explanation": "Directly supported.",
                }
            ]
        }
    )


# =====================
# All required sections present, per template
# =====================


@pytest.mark.parametrize(
    "sections",
    [MOTION_TO_DISMISS_SECTIONS, DEMAND_LETTER_SECTIONS, LEGAL_MEMO_SECTIONS],
    ids=["motion_to_dismiss", "demand_letter", "legal_memorandum"],
)
def test_all_required_sections_present_for_each_template(monkeypatch, sections):
    template_id = _install_fake_template(monkeypatch, sections)
    chunks = [_make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1")]
    irac = _make_irac_result()

    content_by_section = {s["name"]: f"Real drafted content for {s['name']} [1]." for s in sections}
    func = _section_content_function(content_by_section)

    with section_drafter_agent.override(model=FunctionModel(func)), citation_verifier_agent.override(
        model=_verified_citation_verifier()
    ):
        result = draft_document("Test query", irac, chunks, template_id)

    assert isinstance(result, DraftResult)
    assert result.verification_status == "verified"
    assert result.approval_status == "pending_review"
    for section in sections:
        assert section["name"] in result.content_json
        assert result.content_json[section["name"]].strip()


def test_non_required_sections_are_not_drafted(monkeypatch):
    sections = [
        {"name": "Required Section", "description": "Must be drafted.", "required": True},
        {"name": "Optional Section", "description": "May be omitted.", "required": False},
    ]
    template_id = _install_fake_template(monkeypatch, sections)
    chunks = [_make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1")]
    irac = _make_irac_result()

    func = _section_content_function({"Required Section": "Real content [1]."})

    with section_drafter_agent.override(model=FunctionModel(func)), citation_verifier_agent.override(
        model=_verified_citation_verifier()
    ):
        result = draft_document("Test query", irac, chunks, template_id)

    assert "Required Section" in result.content_json
    assert "Optional Section" not in result.content_json


# =====================
# Citation Injector: never introduces a case outside verified_chunks
# =====================


def test_citation_injector_strips_markers_outside_verified_chunks(monkeypatch):
    sections = [{"name": "Argument", "description": "Legal argument.", "required": True}]
    template_id = _install_fake_template(monkeypatch, sections)
    chunks = [_make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1")]
    irac = _make_irac_result()

    def func(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=tool_name,
                    args={"content": "A real citation [1] and a hallucinated one [9]."},
                )
            ]
        )

    with section_drafter_agent.override(model=FunctionModel(func)), citation_verifier_agent.override(
        model=_verified_citation_verifier()
    ):
        result = draft_document("Test query", irac, chunks, template_id)

    assert "[9]" not in result.content_json["Argument"]
    assert "[1]" in result.content_json["Argument"]


# =====================
# verification_status reflects verify_citations' outcome
# =====================


def test_verification_status_verified_on_auto_answer(monkeypatch):
    sections = [{"name": "Argument", "description": "Legal argument.", "required": True}]
    template_id = _install_fake_template(monkeypatch, sections)
    chunks = [_make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1")]
    irac = _make_irac_result()

    func = _section_content_function({"Argument": "Some drafted content [1]."})

    with section_drafter_agent.override(model=FunctionModel(func)), citation_verifier_agent.override(
        model=_verified_citation_verifier()
    ):
        result = draft_document("Test query", irac, chunks, template_id)

    assert result.verification_status == "verified"
    assert result.failure_reason is None
    assert result.approval_status == "pending_review"


def test_verification_status_failed_when_citation_verifier_abstains(monkeypatch):
    sections = [{"name": "Argument", "description": "Legal argument.", "required": True}]
    template_id = _install_fake_template(monkeypatch, sections)
    chunks = [_make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1")]
    irac = _make_irac_result()

    func = _section_content_function({"Argument": "Some drafted content [1]."})

    unverified_model = TestModel(
        custom_output_args={
            "verdicts": [
                {
                    "marker": 1,
                    "isolated_claim": "Some drafted content.",
                    "claim_polarity": "AFFIRMATIVE",
                    "source_polarity": "AFFIRMATIVE",
                    "verdict": "UNVERIFIED",
                    "explanation": "Not supported.",
                }
            ]
        }
    )

    with section_drafter_agent.override(model=FunctionModel(func)), citation_verifier_agent.override(
        model=unverified_model
    ):
        result = draft_document("Test query", irac, chunks, template_id)

    assert result.verification_status == "failed"
    assert result.failure_reason
    assert result.approval_status == "pending_review"


# =====================
# Empty/unfilled required section: surfaced as a failure, not silently returned
# =====================


def test_empty_section_output_surfaces_as_failure_without_calling_verifier(monkeypatch):
    sections = [
        {"name": "Introduction", "description": "Intro.", "required": True},
        {"name": "Argument", "description": "Argument.", "required": True},
    ]
    template_id = _install_fake_template(monkeypatch, sections)
    chunks = [_make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1")]
    irac = _make_irac_result()

    content_by_section = {"Introduction": "", "Argument": "Real content [1]."}
    func = _section_content_function(content_by_section)

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("verify_citations must not be called when a required section is left empty")

    monkeypatch.setattr("app.services.drafting.verify_citations", _should_not_be_called)

    with section_drafter_agent.override(model=FunctionModel(func)):
        result = draft_document("Test query", irac, chunks, template_id)

    assert result.verification_status == "failed"
    assert result.failure_reason and "Introduction" in result.failure_reason
    assert result.approval_status == "pending_review"


# =====================
# Defensive: insufficient_sources input short-circuits before any DB/LLM call
# =====================


def test_insufficient_sources_short_circuits_without_db_or_llm_call(monkeypatch):
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("Should not read the template or call any agent when insufficient_sources is True")

    monkeypatch.setattr("app.services.drafting.get_supabase_client", _should_not_be_called)

    irac = _make_irac_result(
        insufficient_sources=True,
        issue=None,
        rule=None,
        application=None,
        conclusion=None,
        citations=[],
    )
    result = draft_document("Test query", irac, [], uuid.uuid4())

    assert result.verification_status == "failed"
    assert result.content_json == {}
    assert result.approval_status == "pending_review"


# =====================
# Integration (live Openrouter + Supabase, one case per seeded template)
# =====================


@pytest.mark.integration
class TestDraftingIntegration:
    def _check_credentials(self):
        from app.core.config import settings

        assert settings.llm_provider_api_key, "Missing LLM_PROVIDER_API_KEY environment variable"
        assert settings.supabase_url and settings.supabase_service_role_key, "Missing SUPABASE environment variables"

    def _real_research(self, query: str):
        from app.services.reasoning_agent import generate_reasoning
        from app.services.reranker import rerank
        from app.services.retrieval import retrieve
        from app.services.validity_checker import check_validity

        candidates = retrieve(query)
        reranked = rerank(query, candidates)
        valid_chunks = check_validity(reranked).valid_chunks
        reasoning_result = generate_reasoning(query, valid_chunks)
        return reasoning_result, valid_chunks

    def _real_template_id(self, title: str) -> uuid.UUID:
        from app.db.supabase_client import get_supabase_client

        client = get_supabase_client()
        response = client.table("document_templates").select("id").eq("title", title).execute()
        rows = response.data or []
        assert rows, f"No seeded document_templates row found for title={title!r} -- run migration 0004 first."
        return uuid.UUID(rows[0]["id"])

    def _assert_real_draft(self, result: DraftResult, expected_sections: set[str]):
        print(f"\nverification_status={result.verification_status} approval_status={result.approval_status}")
        print(f"failure_reason={result.failure_reason}")
        print(f"sections={list(result.content_json.keys())}")
        assert result.approval_status == "pending_review"
        assert expected_sections.issubset(result.content_json.keys())
        for name, content in result.content_json.items():
            assert content.strip(), f"Section {name!r} was left empty"

    def test_live_motion_to_dismiss_end_to_end(self):
        self._check_credentials()
        query = "What standard governs a warrantless search of a vehicle incident to arrest?"
        reasoning_result, valid_chunks = self._real_research(query)
        if reasoning_result.insufficient_sources or not reasoning_result.citations:
            pytest.skip("No usable research result for this corpus/query to draft from.")

        template_id = self._real_template_id("Motion to Dismiss")
        result = draft_document(query, reasoning_result, valid_chunks, template_id)

        self._assert_real_draft(
            result,
            {"Caption", "Introduction", "Statement of Facts", "Argument", "Conclusion", "Prayer for Relief"},
        )

    def test_live_demand_letter_end_to_end(self):
        self._check_credentials()
        query = "What must a plaintiff show to overcome a qualified immunity defense?"
        reasoning_result, valid_chunks = self._real_research(query)
        if reasoning_result.insufficient_sources or not reasoning_result.citations:
            pytest.skip("No usable research result for this corpus/query to draft from.")

        template_id = self._real_template_id("Demand Letter")
        result = draft_document(query, reasoning_result, valid_chunks, template_id)

        self._assert_real_draft(
            result,
            {"Header", "Introduction", "Statement of Facts", "Legal Basis", "Demand", "Consequences of Non-Compliance"},
        )

    def test_live_legal_memorandum_end_to_end(self):
        self._check_credentials()
        query = "What are the elements of a Section 1983 claim against a state actor?"
        reasoning_result, valid_chunks = self._real_research(query)
        if reasoning_result.insufficient_sources or not reasoning_result.citations:
            pytest.skip("No usable research result for this corpus/query to draft from.")

        template_id = self._real_template_id("Legal Memorandum")
        result = draft_document(query, reasoning_result, valid_chunks, template_id)

        self._assert_real_draft(
            result,
            {"Heading", "Question Presented", "Brief Answer", "Statement of Facts", "Discussion", "Conclusion"},
        )
