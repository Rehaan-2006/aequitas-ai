import pytest
from pydantic_ai.models.test import TestModel

from app.services.query_analyzer import (
    QueryAnalysis,
    QueryType,
    analyze_query,
    query_analyzer_agent,
)


def _mock_result(query: str, query_type: QueryType, reasoning: str, flagged_premise: str | None = None) -> QueryAnalysis:
    """Run analyze_query with the agent's model swapped for a deterministic TestModel."""
    with query_analyzer_agent.override(
        model=TestModel(
            custom_output_args={
                "query_type": query_type.value,
                "reasoning": reasoning,
                "flagged_premise": flagged_premise,
            }
        )
    ):
        return analyze_query(query)


# =====================
# Clear Factual Queries
# =====================


def test_factual_negligence_standard():
    result = _mock_result(
        "What is the standard for negligence in tort law?",
        QueryType.FACTUAL,
        "Asks for an existing legal standard with no false assumption.",
    )
    assert result.query_type == QueryType.FACTUAL
    assert result.flagged_premise is None


def test_factual_section_1983_elements():
    result = _mock_result(
        "What are the elements of a Section 1983 claim?",
        QueryType.FACTUAL,
        "Asks for the elements of a well-established statutory cause of action.",
    )
    assert result.query_type == QueryType.FACTUAL
    assert result.flagged_premise is None


def test_factual_statute_of_limitations():
    result = _mock_result(
        "What is the statute of limitations for fraud claims in federal court?",
        QueryType.FACTUAL,
        "Asks for a specific, verifiable legal timeframe.",
    )
    assert result.query_type == QueryType.FACTUAL
    assert result.flagged_premise is None


# =====================
# Clear False-Premise Queries
# =====================


def test_false_premise_overruled_case_treated_as_good_law():
    result = _mock_result(
        "Since Roe v. Wade guarantees an unconditional federal right to abortion "
        "nationwide today, what state restrictions would still be unconstitutional?",
        QueryType.FALSE_PREMISE,
        "Roe v. Wade was overruled by Dobbs v. Jackson Women's Health Organization "
        "in 2022 and is no longer good law.",
        flagged_premise="Roe v. Wade no longer guarantees a federal right to abortion; "
        "it was overruled by Dobbs v. Jackson Women's Health Organization (2022).",
    )
    assert result.query_type == QueryType.FALSE_PREMISE
    assert result.flagged_premise is not None
    assert "Dobbs" in result.flagged_premise


def test_false_premise_misstated_miranda_holding():
    result = _mock_result(
        "Given that Miranda v. Arizona no longer requires police to inform suspects "
        "of their right to remain silent, what should officers say instead?",
        QueryType.FALSE_PREMISE,
        "Miranda v. Arizona remains good law; the query misstates its holding.",
        flagged_premise="Miranda v. Arizona has not been overruled and still requires "
        "the standard Miranda warnings.",
    )
    assert result.query_type == QueryType.FALSE_PREMISE
    assert result.flagged_premise is not None


def test_false_premise_nonexistent_rule():
    result = _mock_result(
        "Since Federal Rule of Civil Procedure 56 requires a jury trial before summary "
        "judgment can be granted, how do parties request one?",
        QueryType.FALSE_PREMISE,
        "Rule 56 governs summary judgment specifically to avoid a jury trial when there "
        "is no genuine dispute of material fact; the query inverts the rule's purpose.",
        flagged_premise="Rule 56 does not require a jury trial before summary judgment; "
        "summary judgment is granted precisely when no trial is needed.",
    )
    assert result.query_type == QueryType.FALSE_PREMISE
    assert result.flagged_premise is not None


# =====================
# Clear Exploratory Queries
# =====================


def test_exploratory_qualified_immunity_policy():
    result = _mock_result(
        "What are the arguments for and against qualified immunity reform?",
        QueryType.EXPLORATORY,
        "Open-ended request for competing policy arguments, no single premise to check.",
    )
    assert result.query_type == QueryType.EXPLORATORY
    assert result.flagged_premise is None


def test_exploratory_free_speech_defamation_tension():
    result = _mock_result(
        "How have courts approached the tension between free speech and defamation over time?",
        QueryType.EXPLORATORY,
        "Broad, comparative question spanning many cases rather than one factual claim.",
    )
    assert result.query_type == QueryType.EXPLORATORY
    assert result.flagged_premise is None


# =====================
# Ambiguous Edge Cases
# =====================


def test_ambiguous_validity_question_classified_factual():
    # "Is X still good law?" reads like it could be false-premise-shaped, but
    # asking about validity itself (rather than assuming an answer) is a
    # legitimate factual question -- exercises the boundary between the two.
    result = _mock_result(
        "Is the exclusionary rule still good law?",
        QueryType.FACTUAL,
        "Asks whether a doctrine is still valid rather than assuming an answer.",
    )
    assert result.query_type == QueryType.FACTUAL
    assert result.flagged_premise is None


def test_ambiguous_circuit_split_classified_exploratory():
    # Sits between FACTUAL (asking what the law is) and EXPLORATORY (asking
    # about an unresolved, multi-sided split with no single correct answer).
    result = _mock_result(
        "What's the current circuit split on personal jurisdiction over foreign defendants?",
        QueryType.EXPLORATORY,
        "Describes an unresolved multi-circuit split rather than a single settled rule.",
    )
    assert result.query_type == QueryType.EXPLORATORY
    assert result.flagged_premise is None


def test_analyze_query_returns_query_analysis_instance():
    result = _mock_result(
        "What is the standard for negligence in tort law?",
        QueryType.FACTUAL,
        "Sanity check on the return type.",
    )
    assert isinstance(result, QueryAnalysis)


# =====================
# Integration (live Openrouter, ~5 hand-picked cases)
# =====================


@pytest.mark.integration
class TestQueryAnalyzerIntegration:
    def _check_credentials(self):
        from app.core.config import settings

        assert settings.llm_provider_api_key, "Missing LLM_PROVIDER_API_KEY environment variable"

    def test_live_factual_negligence(self):
        self._check_credentials()
        result = analyze_query("What is the standard for negligence in tort law?")
        print(f"\nquery_type={result.query_type} reasoning={result.reasoning}")
        assert result.query_type == QueryType.FACTUAL

    def test_live_factual_title_vii(self):
        self._check_credentials()
        result = analyze_query(
            "What must a plaintiff show to establish a prima facie case of "
            "employment discrimination under Title VII?"
        )
        print(f"\nquery_type={result.query_type} reasoning={result.reasoning}")
        assert result.query_type == QueryType.FACTUAL

    def test_live_false_premise_overruled_roe(self):
        self._check_credentials()
        result = analyze_query(
            "Since Roe v. Wade guarantees an unconditional federal right to abortion "
            "nationwide today, what state restrictions would still be unconstitutional?"
        )
        print(f"\nquery_type={result.query_type} flagged_premise={result.flagged_premise}")
        assert result.query_type == QueryType.FALSE_PREMISE
        assert result.flagged_premise

    def test_live_false_premise_miranda(self):
        self._check_credentials()
        result = analyze_query(
            "Given that Miranda v. Arizona no longer requires police to inform suspects "
            "of their right to remain silent, what should officers say instead?"
        )
        print(f"\nquery_type={result.query_type} flagged_premise={result.flagged_premise}")
        assert result.query_type == QueryType.FALSE_PREMISE
        assert result.flagged_premise

    def test_live_exploratory_qualified_immunity(self):
        self._check_credentials()
        result = analyze_query("What are the competing policy considerations behind qualified immunity doctrine?")
        print(f"\nquery_type={result.query_type} reasoning={result.reasoning}")
        assert result.query_type == QueryType.EXPLORATORY
