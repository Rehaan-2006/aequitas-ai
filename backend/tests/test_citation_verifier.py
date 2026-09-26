"""
Module 6 -- Citation Verifier Agent tests.

Mocked suite (default CI, no API cost): swaps citation_verifier_agent's
model for a deterministic pydantic_ai TestModel, same pattern as
tests/test_reasoning_agent.py. Aggregation (per-marker verdicts -> claim
grouping -> AUTO_ANSWER/ABSTAIN) happens outside the LLM call, so these
tests exercise the real aggregation logic against a mocked per-marker
verdict draft, not a second mock of the aggregation itself.

Integration tests at the bottom require live Openrouter + Supabase
credentials and run the real Module 3 -> 3.5 -> 4 -> 5 -> 6 chain;
excluded from default CI (`pytest -m "not integration"`). Includes two
adversarial LePhantomCite-style cases: a real Module 5 output with one
claim's text deliberately altered to misstate its cited source, checked
that this module's real model catches it rather than rubber-stamping
it. The first corruption is a blunt, self-announcing one ("Contrary to
the source..."); the second is subtler -- a single word/phrase flip
inside the model's own real sentence (e.g. "requires" -> "does not
require", "reasonable suspicion" -> "probable cause") with no phrase
that tips off a superficial reader, testing whether the verifier is
doing real entailment rather than pattern-matching a "this is wrong"
tell.
"""

import re

import pytest
from pydantic_ai.models.test import TestModel

from app.services.citation_verifier import (
    CitationVerifierResult,
    citation_verifier_agent,
    verify_citations,
)
from app.services.reasoning_agent import Citation, ReasoningResult
from app.services.retrieval import CaseChunk


def _make_chunk(case_id: str, case_name: str, citation: str, chunk_text: str) -> CaseChunk:
    return CaseChunk(
        id=f"chunk-{case_id}",
        case_id=case_id,
        chunk_index=0,
        chunk_text=chunk_text,
        case_name=case_name,
        citation=citation,
    )


def _make_citation(marker: int, chunk: CaseChunk) -> Citation:
    return Citation(
        marker=marker,
        case_id=chunk.case_id,
        case_name=chunk.case_name,
        citation=chunk.citation,
        chunk_id=chunk.id,
    )


# Ordered so a negated form (e.g. "may not", "is not required") is checked
# before its bare counterpart -- otherwise the bare pattern would match
# inside the negated phrase and produce a double negative instead of a
# clean flip. Only the first matching pair in the text is applied, so each
# corruption changes exactly one specific standard/outcome, not several.
_MEANING_FLIP_PAIRS = [
    (r"\bwithout a warrant\b", "with a warrant"),
    (r"\bwith a warrant\b", "without a warrant"),
    (r"\bdoes not require\b", "requires"),
    (r"\brequires\b", "does not require"),
    (r"\bis not required\b", "is required"),
    (r"\bis required\b", "is not required"),
    (r"\bmay not\b", "may"),
    (r"\bmay\b", "may not"),
    (r"\bpermits\b", "prohibits"),
    (r"\bprohibits\b", "permits"),
    (r"\breasonable suspicion\b", "probable cause"),
    (r"\bprobable cause\b", "reasonable suspicion"),
    (r"\bunreasonable\b", "reasonable"),
    (r"\breasonable\b", "unreasonable"),
    (r"\bunlawful\b", "lawful"),
    (r"\blawful\b", "unlawful"),
    (r"\binvalid\b", "valid"),
    (r"\bvalid\b", "invalid"),
]


def _flip_one_legal_term(text: str) -> tuple[str, int, int] | None:
    """
    Finds the first pattern (in priority order) present in `text` and
    swaps it for its opposite, leaving everything else -- sentence
    structure, tone, surrounding claim -- untouched. Returns None if no
    known flip pattern is present, so the caller can pick a different
    field or skip rather than fabricate an unnatural corruption.

    Returns (flipped_text, match_start, match_end) rather than just the
    flipped text -- match_start/match_end are positions in the *original*
    text, needed by the caller to work out which specific marker the flip
    actually landed next to (a field can carry several markers, each tied
    to a different sub-claim, so the caller must not assume the flip
    affected whichever marker happens to be listed first).
    """
    for pattern, replacement in _MEANING_FLIP_PAIRS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            flipped = text[: match.start()] + replacement + text[match.end() :]
            return flipped, match.start(), match.end()
    return None


# Narrower than _MEANING_FLIP_PAIRS -- restricted to requirement/applicability
# directionality specifically (as opposed to permission/prohibition or the
# reasonable-suspicion/probable-cause standard), so the second adversarial
# test below exercises a genuinely different flip category rather than
# happening to land on the same kind of corruption as the first test.
_REQUIREMENT_FLIP_PAIRS = [
    (r"\bdoes not require\b", "requires"),
    (r"\brequires\b", "does not require"),
    (r"\bis not required\b", "is required"),
    (r"\bis required\b", "is not required"),
    (r"\bdoes not apply\b", "applies"),
    (r"\bapplies\b", "does not apply"),
    (r"\bdoes not extend\b", "extends"),
    (r"\bextends\b", "does not extend"),
]


# Concessive/conditional framings ("whether or not X applies", "regardless of
# whether X applies", "even if X applies") already cover both polarities of
# whatever follows -- negating the embedded clause inside one of these doesn't
# actually reverse the sentence's asserted meaning, so a match inside one of
# these isn't a genuine semantic corruption and must be skipped in favor of a
# different occurrence/pattern.
_CONCESSIVE_MARKERS = ("whether", "regardless", "even if")


def _is_genuine_reversal_site(text: str, start: int, end: int) -> bool:
    sentence_start = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    sentence_end = text.find(".", end)
    if sentence_end == -1:
        sentence_end = len(text)
    sentence = text[sentence_start:sentence_end].lower()
    return not any(marker in sentence for marker in _CONCESSIVE_MARKERS)


def _flip_one_requirement_term(text: str) -> tuple[str, int, int] | None:
    """Same contract as _flip_one_legal_term (returns flipped_text, match_start,
    match_end), restricted to the requirement/applicability pattern set."""
    for pattern, replacement in _REQUIREMENT_FLIP_PAIRS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if not _is_genuine_reversal_site(text, match.start(), match.end()):
                continue
            flipped = text[: match.start()] + replacement + text[match.end() :]
            return flipped, match.start(), match.end()
    return None


_MARKER_TAG_PATTERN = re.compile(r"\[(\d+)\]")


def _nearest_marker(text: str, position: int) -> int | None:
    """
    Finds which citation marker a flip at `position` actually belongs to.
    Module 5's own convention (visible in every real reasoning output so
    far) is that a marker trails the specific claim it supports -- e.g.
    "...permits a full search on probable cause [2]" -- so the marker
    immediately following the flip position is preferred; only if none
    follows (the flip is after the last marker in the field) does this
    fall back to the nearest preceding one.
    """
    after = _MARKER_TAG_PATTERN.search(text, position)
    if after:
        return int(after.group(1))
    before_matches = list(_MARKER_TAG_PATTERN.finditer(text[:position]))
    if before_matches:
        return int(before_matches[-1].group(1))
    return None


def _run_with_mock(reasoning_result: ReasoningResult, valid_chunks: list[CaseChunk], verdicts: list[dict]) -> CitationVerifierResult:
    with citation_verifier_agent.override(model=TestModel(custom_output_args={"verdicts": verdicts})):
        return verify_citations(reasoning_result, valid_chunks)


def _run_sync_should_not_be_called(*args, **kwargs):
    raise AssertionError("citation_verifier_agent.run_sync should not have been called on a short-circuit path")


def _assert_no_llm_call(monkeypatch, reasoning_result, valid_chunks) -> CitationVerifierResult:
    """Proves the short-circuit path never reaches the LLM, rather than just
    happening to produce the right-looking output despite an available
    (mocked) model -- monkeypatches run_sync itself to raise if invoked."""
    monkeypatch.setattr(citation_verifier_agent, "run_sync", _run_sync_should_not_be_called)
    return verify_citations(reasoning_result, valid_chunks)


# =====================
# Deterministic short-circuits (no LLM call)
# =====================


def test_insufficient_sources_short_circuits_without_llm_call(monkeypatch):
    reasoning_result = ReasoningResult(insufficient_sources=True, insufficient_sources_reason="No sources at all.")
    result = _assert_no_llm_call(monkeypatch, reasoning_result, [])

    assert result.outcome == "ABSTAIN"
    assert result.verdicts == []
    assert result.abstain_reason and "insufficient sources" in result.abstain_reason.lower()


def test_zero_citations_on_non_abstaining_answer_short_circuits_without_llm_call(monkeypatch):
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        issue="Some issue with no citations at all.",
        rule="Some rule.",
        application="Some application.",
        conclusion="Some conclusion.",
        citations=[],
    )
    result = _assert_no_llm_call(monkeypatch, reasoning_result, [])

    assert result.outcome == "ABSTAIN"
    assert result.verdicts == []
    assert result.abstain_reason and "suspicious" in result.abstain_reason.lower()


# =====================
# All-VERIFIED -> AUTO_ANSWER
# =====================


def test_all_verified_resolves_to_auto_answer():
    chunk_a = _make_chunk("case-a", "Terry v. Ohio", "392 U.S. 1", "An officer may stop and frisk on reasonable suspicion.")
    chunk_b = _make_chunk("case-b", "United States v. Ross", "456 U.S. 798", "The automobile exception permits a full search on probable cause.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        issue="Whether police may search a vehicle without a warrant.",
        rule="An officer may conduct a limited search on reasonable suspicion [1].",
        application="Here, the automobile exception permits a full search on probable cause [2].",
        conclusion="The search is lawful [1][2].",
        citations=[_make_citation(1, chunk_a), _make_citation(2, chunk_b)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk_a, chunk_b],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "An officer may conduct a limited search on reasonable suspicion.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "VERIFIED",
                "explanation": "Source directly supports the reasonable-suspicion standard.",
            },
            {
                "marker": 2,
                "isolated_claim": "The automobile exception permits a full search on probable cause.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "VERIFIED",
                "explanation": "Source directly supports the automobile exception.",
            },
        ],
    )

    assert result.outcome == "AUTO_ANSWER"
    assert result.abstain_reason is None
    assert [v.verdict for v in result.verdicts] == ["VERIFIED", "VERIFIED"]
    assert result.verdicts[0].case_name == "Terry v. Ohio"
    assert result.verdicts[1].case_id == "case-b"


# =====================
# Single UNVERIFIED -> ABSTAIN
# =====================


def test_single_unverified_resolves_to_abstain():
    chunk = _make_chunk("case-a", "Case A", "1 U.S. 1", "Discusses an unrelated procedural issue.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="A claim not really supported by the source [1].",
        citations=[_make_citation(1, chunk)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "A claim not really supported by the source.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "UNVERIFIED",
                "explanation": "Source does not address this claim.",
            }
        ],
    )

    assert result.outcome == "ABSTAIN"
    assert result.verdicts[0].verdict == "UNVERIFIED"
    assert result.abstain_reason and "[1]" in result.abstain_reason


# =====================
# Single CONTRADICTED -> ABSTAIN
# =====================


def test_single_contradicted_resolves_to_abstain():
    chunk = _make_chunk("case-a", "Case A", "1 U.S. 1", "The court held that no warrant is required in this scenario.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="A warrant is required in this scenario [1].",
        citations=[_make_citation(1, chunk)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "A warrant is required in this scenario.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "CONTRADICTED",
                "explanation": "Source holds the opposite -- no warrant required.",
            }
        ],
    )

    assert result.outcome == "ABSTAIN"
    assert result.verdicts[0].verdict == "CONTRADICTED"
    assert result.abstain_reason and "[1]" in result.abstain_reason


# =====================
# Multi-source claim: OR logic, both verdicts still reported
# =====================


def test_multi_source_claim_or_logic_still_reports_both_verdicts():
    chunk_a = _make_chunk("case-a", "Case A", "1 U.S. 1", "Directly supports the claim.")
    chunk_b = _make_chunk("case-b", "Case B", "2 U.S. 2", "Does not really address the claim.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="A claim backed by two sources at once [1][2].",
        citations=[_make_citation(1, chunk_a), _make_citation(2, chunk_b)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk_a, chunk_b],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "A claim backed by two sources at once.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "VERIFIED",
                "explanation": "Directly on point.",
            },
            {
                "marker": 2,
                "isolated_claim": "A claim backed by two sources at once.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "UNVERIFIED",
                "explanation": "Ambiguous overlap with the claim.",
            },
        ],
    )

    assert result.outcome == "AUTO_ANSWER"
    assert len(result.verdicts) == 2
    assert {v.marker: v.verdict for v in result.verdicts} == {1: "VERIFIED", 2: "UNVERIFIED"}


def test_multi_source_claim_fails_when_no_source_in_group_verified():
    chunk_a = _make_chunk("case-a", "Case A", "1 U.S. 1", "Off point.")
    chunk_b = _make_chunk("case-b", "Case B", "2 U.S. 2", "Also off point.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="A claim backed by two sources at once [1][2].",
        citations=[_make_citation(1, chunk_a), _make_citation(2, chunk_b)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk_a, chunk_b],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "A claim backed by two sources at once.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "UNVERIFIED",
                "explanation": "Doesn't support the claim.",
            },
            {
                "marker": 2,
                "isolated_claim": "A claim backed by two sources at once.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "UNVERIFIED",
                "explanation": "Doesn't support the claim either.",
            },
        ],
    )

    assert result.outcome == "ABSTAIN"
    assert len(result.verdicts) == 2


# =====================
# Defensive: model omits a verdict for a marker present in the text
# =====================


def test_missing_verdict_for_a_marker_defaults_to_unverified():
    chunk = _make_chunk("case-a", "Case A", "1 U.S. 1", "Some text.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="A claim with a marker the model forgets to judge [1].",
        citations=[_make_citation(1, chunk)],
    )

    result = _run_with_mock(reasoning_result, [chunk], verdicts=[])

    assert result.outcome == "ABSTAIN"
    assert len(result.verdicts) == 1
    assert result.verdicts[0].verdict == "UNVERIFIED"


def test_verify_citations_returns_result_instance():
    chunk = _make_chunk("case-a", "Case A", "1 U.S. 1", "Some text.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="Sanity check claim [1].",
        citations=[_make_citation(1, chunk)],
    )
    result = _run_with_mock(
        reasoning_result,
        [chunk],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "Sanity check claim.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "VERIFIED",
                "explanation": "ok",
            }
        ],
    )
    assert isinstance(result, CitationVerifierResult)


# =====================
# Code-enforced polarity override: model's own verdict is never trusted over
# a claim_polarity/source_polarity mismatch it reports itself
# =====================


def test_polarity_mismatch_forces_contradicted_even_if_model_says_verified():
    chunk = _make_chunk("case-a", "Case A", "1 U.S. 1", "The exception permits the search.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="The exception prohibits the search [1].",
        citations=[_make_citation(1, chunk)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "The exception prohibits the search.",
                "claim_polarity": "NEGATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "VERIFIED",
                "explanation": "Directionality matches (model's own, incorrect, self-assessment).",
            }
        ],
    )

    assert result.outcome == "ABSTAIN"
    assert result.verdicts[0].verdict == "CONTRADICTED"
    assert "Overridden to CONTRADICTED" in result.verdicts[0].explanation
    assert "NEGATIVE" in result.verdicts[0].explanation and "AFFIRMATIVE" in result.verdicts[0].explanation


def test_matching_polarity_does_not_override_model_verdict():
    chunk = _make_chunk("case-a", "Case A", "1 U.S. 1", "The exception permits the search.")
    reasoning_result = ReasoningResult(
        insufficient_sources=False,
        rule="The exception permits the search [1].",
        citations=[_make_citation(1, chunk)],
    )

    result = _run_with_mock(
        reasoning_result,
        [chunk],
        verdicts=[
            {
                "marker": 1,
                "isolated_claim": "The exception permits the search.",
                "claim_polarity": "AFFIRMATIVE",
                "source_polarity": "AFFIRMATIVE",
                "verdict": "VERIFIED",
                "explanation": "Directly supports the claim.",
            }
        ],
    )

    assert result.outcome == "AUTO_ANSWER"
    assert result.verdicts[0].verdict == "VERIFIED"
    assert result.verdicts[0].explanation == "Directly supports the claim."


# =====================
# Integration (live Openrouter + Supabase, ~5 hand-picked cases)
# =====================


@pytest.mark.integration
class TestCitationVerifierIntegration:
    def _check_credentials(self):
        from app.core.config import settings

        assert settings.llm_provider_api_key, "Missing LLM_PROVIDER_API_KEY environment variable"
        assert settings.supabase_url and settings.supabase_service_role_key, "Missing SUPABASE environment variables"

    def _real_reasoning_result(self, query: str):
        from app.services.reasoning_agent import generate_reasoning
        from app.services.reranker import rerank
        from app.services.retrieval import retrieve
        from app.services.validity_checker import check_validity

        candidates = retrieve(query)
        reranked = rerank(query, candidates)
        valid_chunks = check_validity(reranked).valid_chunks
        reasoning_result = generate_reasoning(query, valid_chunks)
        return reasoning_result, valid_chunks

    def test_live_factual_fourth_amendment_vehicle_search(self):
        self._check_credentials()
        query = "What standard governs a warrantless search of a vehicle incident to arrest?"
        reasoning_result, valid_chunks = self._real_reasoning_result(query)
        result = verify_citations(reasoning_result, valid_chunks)
        print(f"\noutcome={result.outcome} verdicts={[(v.marker, v.verdict) for v in result.verdicts]}")
        assert result.outcome in ("AUTO_ANSWER", "ABSTAIN")
        if reasoning_result.insufficient_sources:
            assert result.outcome == "ABSTAIN"

    def test_live_factual_qualified_immunity(self):
        self._check_credentials()
        query = "What must a plaintiff show to overcome a qualified immunity defense?"
        reasoning_result, valid_chunks = self._real_reasoning_result(query)
        result = verify_citations(reasoning_result, valid_chunks)
        print(f"\noutcome={result.outcome} verdicts={[(v.marker, v.verdict) for v in result.verdicts]}")
        assert result.outcome in ("AUTO_ANSWER", "ABSTAIN")

    def test_live_factual_section_1983(self):
        self._check_credentials()
        query = "What are the elements of a Section 1983 claim against a state actor?"
        reasoning_result, valid_chunks = self._real_reasoning_result(query)
        result = verify_citations(reasoning_result, valid_chunks)
        print(f"\noutcome={result.outcome} verdicts={[(v.marker, v.verdict) for v in result.verdicts]}")
        assert result.outcome in ("AUTO_ANSWER", "ABSTAIN")

    def test_live_false_premise_miranda(self):
        self._check_credentials()
        from app.services.reasoning_agent import generate_reasoning
        from app.services.reranker import rerank
        from app.services.retrieval import retrieve
        from app.services.validity_checker import check_validity

        query = "Given that Miranda no longer requires warnings, what should officers say?"
        flagged_premise = "Miranda v. Arizona has not been overruled and still requires warnings."
        candidates = retrieve(query)
        reranked = rerank(query, candidates)
        valid_chunks = check_validity(reranked).valid_chunks
        reasoning_result = generate_reasoning(query, valid_chunks, flagged_premise=flagged_premise)
        result = verify_citations(reasoning_result, valid_chunks)
        print(f"\noutcome={result.outcome} verdicts={[(v.marker, v.verdict) for v in result.verdicts]}")
        assert result.outcome in ("AUTO_ANSWER", "ABSTAIN")

    def test_live_adversarial_corrupted_claim_is_caught(self):
        """
        LePhantomCite-style methodology: take a real Module 5 output with a
        genuine, well-supported claim, then deliberately corrupt that claim's
        text to state the opposite of what its cited source actually says.
        A verifier that only ever sees already-correct output never proves
        it can catch a failure -- this confirms the real model does.
        """
        self._check_credentials()
        query = "What standard governs a warrantless search of a vehicle incident to arrest?"
        reasoning_result, valid_chunks = self._real_reasoning_result(query)

        if reasoning_result.insufficient_sources or not reasoning_result.citations:
            pytest.skip("No citations available on this corpus/query to corrupt for the adversarial case.")

        marker = reasoning_result.citations[0].marker
        corrupted_claim = (
            f"Contrary to the source, no warrant exception of any kind applies to vehicle "
            f"searches under any circumstances [{marker}]."
        )
        corrupted_result = reasoning_result.model_copy(update={"rule": corrupted_claim})

        result = verify_citations(corrupted_result, valid_chunks)
        print(f"\ncorrupted claim outcome={result.outcome} verdicts={[(v.marker, v.verdict) for v in result.verdicts]}")

        corrupted_marker_verdict = next(v.verdict for v in result.verdicts if v.marker == marker)
        assert corrupted_marker_verdict in ("UNVERIFIED", "CONTRADICTED")
        assert result.outcome == "ABSTAIN"

    def test_live_adversarial_requirement_flip_is_caught(self):
        """
        Second subtle polarity-flip variant, deliberately restricted to a
        different flip category than test_live_adversarial_subtle_flip_is_caught
        (requirement/applicability directionality -- "requires" <-> "does not
        require", "applies" <-> "does not apply" -- rather than permission/
        prohibition or the reasonable-suspicion/probable-cause standard), so a
        pass here isn't just re-confirming the same fix on the same sentence
        shape. Same no-tell-phrase constraint as the other adversarial tests.

        KNOWN INCONCLUSIVE (as of the structural claim_polarity/source_polarity
        fix in verify_citations): across three live re-runs, the first
        "requires"/"does not require" match in the real generated rule text
        consistently lands inside a "the Fourth Amendment generally requires
        X, but the Supreme Court has recognized certain exceptions..." sentence.
        The trailing exception clause discusses the same warrant-requirement
        topic regardless of which way the leading clause is flipped, so the
        isolated claim's overall meaning stays partially congruent with the
        source either way -- this is the same category of ambiguous flip-site
        problem as the earlier "whether or not X applies" concessive-clause
        bug, just not one caught by the current three-keyword concessive
        filter. This is a test-construction problem, not a confirmed second
        gap in the fix: it needs a cleaner, self-contained flip site (same
        standard as the Chimel case in test_live_adversarial_subtle_flip_is_caught)
        in a future session, not more live retries against the current one.
        """
        self._check_credentials()
        query = "What standard governs a warrantless search of a vehicle incident to arrest?"
        reasoning_result, valid_chunks = self._real_reasoning_result(query)

        if reasoning_result.insufficient_sources or not reasoning_result.citations:
            pytest.skip("No citations available on this corpus/query to corrupt for the adversarial case.")

        field_names = ["rule", "application", "conclusion", "issue"]
        corrupted_field = corrupted_text = target_marker = None
        for field_name in field_names:
            original_text = getattr(reasoning_result, field_name)
            if not original_text:
                continue
            flip = _flip_one_requirement_term(original_text)
            if flip is None:
                continue
            flipped_text, start, _end = flip
            nearest = _nearest_marker(original_text, start)
            if nearest is None:
                continue
            corrupted_field, corrupted_text, target_marker = field_name, flipped_text, nearest
            break

        if corrupted_field is None:
            pytest.skip(
                "No requirement/applicability flip pattern matched the real claim text for this "
                "corpus run -- cannot construct this corruption variant without fabricating phrasing."
            )

        corrupted_result = reasoning_result.model_copy(update={corrupted_field: corrupted_text})

        result = verify_citations(corrupted_result, valid_chunks)
        print(f"\nrequirement-flip corrupted field={corrupted_field}")
        print(f"corrupted claim text={corrupted_text}")
        print(f"outcome={result.outcome} verdicts={[(v.marker, v.verdict, v.explanation) for v in result.verdicts]}")

        corrupted_marker_verdict = next(v.verdict for v in result.verdicts if v.marker == target_marker)
        assert corrupted_marker_verdict in ("UNVERIFIED", "CONTRADICTED")
        assert result.outcome == "ABSTAIN"

    def test_live_adversarial_subtle_flip_is_caught(self):
        """
        Subtler variant of the adversarial case above. The blunt version's
        "Contrary to the source, ... under any circumstances" phrasing is a
        self-announcing tell -- a verifier could catch it via superficial
        lexical pattern-matching ("this text sounds like a denial") without
        ever really comparing claim content to source content.

        This version takes a real, correctly-supported claim and flips a
        single specific legal term inside the model's own real sentence
        (e.g. "requires" <-> "does not require", "reasonable suspicion" <->
        "probable cause", "without a warrant" <-> "with a warrant") --
        no "contrary to," "actually," or any other suspicious-sounding
        marker. The corrupted sentence reads with the same confident,
        ordinary tone a genuine claim would, because it *is* the genuine
        sentence except for that one flipped term.
        """
        self._check_credentials()
        query = "What standard governs a warrantless search of a vehicle incident to arrest?"
        reasoning_result, valid_chunks = self._real_reasoning_result(query)

        if reasoning_result.insufficient_sources or not reasoning_result.citations:
            pytest.skip("No citations available on this corpus/query to corrupt for the adversarial case.")

        field_names = ["rule", "application", "conclusion", "issue"]

        corrupted_field = corrupted_text = target_marker = None
        for field_name in field_names:
            original_text = getattr(reasoning_result, field_name)
            if not original_text:
                continue
            flip = _flip_one_legal_term(original_text)
            if flip is None:
                continue
            flipped_text, start, _end = flip
            nearest = _nearest_marker(original_text, start)
            if nearest is None:
                continue
            corrupted_field, corrupted_text, target_marker = field_name, flipped_text, nearest
            break

        if corrupted_field is None:
            pytest.skip(
                "No known meaning-flip pattern matched the real claim text for this query/corpus run -- "
                "cannot construct a subtle corruption without fabricating unnatural phrasing."
            )

        corrupted_result = reasoning_result.model_copy(update={corrupted_field: corrupted_text})

        result = verify_citations(corrupted_result, valid_chunks)
        print(f"\nsubtly corrupted field={corrupted_field}")
        print(f"corrupted claim text={corrupted_text}")
        print(f"outcome={result.outcome} verdicts={[(v.marker, v.verdict, v.explanation) for v in result.verdicts]}")

        corrupted_marker_verdict = next(v.verdict for v in result.verdicts if v.marker == target_marker)
        assert corrupted_marker_verdict in ("UNVERIFIED", "CONTRADICTED")
        assert result.outcome == "ABSTAIN"
