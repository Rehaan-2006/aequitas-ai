"""
Aequitas AI — Eval harness, step 3: score results.

All three tasks (Court, Citation, Quotation) score via deterministic
string matching — no LLM judge, no extra cost. Reports, per model and
task: schema-failure rate (pipeline couldn't produce structured output
at all — kept separate from hallucination, see run_harness.py), abstain
rate, and accuracy among the answers the pipeline actually attempted.

Run: python3 score_results.py
"""

import glob
import json
import re
from difflib import SequenceMatcher


def normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fuzzy_contains(haystack: str, needle: str, threshold: float = 0.6) -> bool:
    """True if needle appears in haystack (normalized substring), or is a
    close enough match via SequenceMatcher for minor phrasing differences.
    Conservative on purpose -- undercounting is safer than overcounting
    for a hallucination-rate claim."""
    h, n = normalize(haystack), normalize(needle)
    if not n:
        return False
    if n in h:
        return True
    return SequenceMatcher(None, h, n).ratio() >= threshold


def score_court(record: dict, ground_truth: str) -> bool:
    return fuzzy_contains(record["answer_text"], ground_truth)


def score_citation(record: dict, ground_truth: str) -> bool:
    # Citations are precise strings -- stricter normalized containment,
    # no fuzzy ratio fallback (avoid false "matches" on similar-looking
    # but wrong reporter volumes/pages).
    return normalize(ground_truth) in normalize(record["answer_text"])


def score_quotation(record: dict, raw_text: str) -> bool:
    """True if any sufficiently long sentence-like span of the pipeline's
    answer appears verbatim (normalized) inside the real opinion text --
    a genuine quote, not a paraphrase or fabrication."""
    normalized_source = normalize(raw_text)
    answer = record["answer_text"]
    # naive sentence split; good enough for a verbatim-substring check
    spans = re.split(r"(?<=[.!?])\s+", answer)
    for span in spans:
        if len(span) < 30:
            continue
        if normalize(span) in normalized_source:
            return True
    return False


SCORERS = {
    "court": score_court,
    "citation": score_citation,
    "quotation": score_quotation,
}


def load_questions_by_id(path: str = "eval_questions.json") -> dict:
    with open(path) as f:
        questions = json.load(f)
    return {q["question_id"]: q for q in questions}


def score_file(path: str, questions_by_id: dict) -> dict:
    stats = {}  # task -> {"total": n, "schema_failure": n, "abstained": n, "correct": n, "attempted": n}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            task = record["task"]
            q = questions_by_id.get(record["question_id"])
            if q is None:
                continue

            s = stats.setdefault(
                task, {"total": 0, "schema_failure": 0, "abstained": 0, "correct": 0, "attempted": 0}
            )
            s["total"] += 1

            if record["schema_failure"]:
                s["schema_failure"] += 1
                continue

            if record["outcome"] != "answered":
                s["abstained"] += 1
                continue

            s["attempted"] += 1
            scorer = SCORERS[task]
            if scorer(record, q["ground_truth"]):
                s["correct"] += 1

    return stats


def print_summary(model: str, stats: dict):
    print(f"\n=== {model} ===")
    for task, s in stats.items():
        accuracy = (s["correct"] / s["attempted"] * 100) if s["attempted"] else float("nan")
        print(
            f"  {task:10s} total={s['total']:3d}  "
            f"schema_failures={s['schema_failure']:3d}  "
            f"abstained={s['abstained']:3d}  "
            f"attempted={s['attempted']:3d}  "
            f"correct={s['correct']:3d}  "
            f"accuracy={accuracy:.1f}%"
        )


def main():
    questions_by_id = load_questions_by_id()
    for path in sorted(glob.glob("results/run_*.jsonl")):
        model = path.replace("results/run_", "").replace(".jsonl", "").replace("_", "/")
        stats = score_file(path, questions_by_id)
        print_summary(model, stats)


if __name__ == "__main__":
    main()
