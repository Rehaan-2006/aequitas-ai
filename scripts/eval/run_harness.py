"""
Aequitas AI — Eval harness, step 2: run the pipeline across models.

!! VERIFY BEFORE RUNNING !!
This overrides each agent's model via PydanticAI's `.override(model=...)`
context manager (the same pattern test_drafting.py / test_citation_verifier.py
already use), rather than editing .env per model. This requires importing
the actual module-level Agent instances from each service file.
`citation_verifier_agent` in services/citation_verifier.py is confirmed by
name from earlier test code. `query_analyzer_agent` (services/query_analyzer.py)
and the reasoning agent's variable name (services/reasoning_agent.py) are
NOT independently confirmed here — grep each file for "= Agent(" and fix
the import names below if they differ before running anything.

Writes one JSONL line per (model, question) pair immediately after each
run, so a crash or a Colab-style disconnect loses nothing already
completed. Re-running skips pairs already present in the output file.

Logs a question's run as a schema_failure (separately from a normal
ABSTAIN) when the pipeline itself raises rather than returning a result —
this matters for the weaker/older models, where a high failure rate may
reflect inability to follow the structured-output schema rather than the
architecture catching a hallucination. See docs/DECISIONS.md.

Run: python3 run_harness.py
"""

import json
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.getcwd())

from app.services.pipeline import run_pipeline  # noqa: E402

# VERIFY these three imports match the real variable names before running:
from app.services.query_analyzer import query_analyzer_agent  # noqa: E402
from app.services.reasoning_agent import reasoning_agent  # noqa: E402  -- confirm name
from app.services.citation_verifier import citation_verifier_agent  # noqa: E402

QUESTIONS_PATH = "../scripts/eval/eval_questions.json"
RESULTS_DIR = "../scripts/eval/results"

DEFAULT_MODELS = ["anthropic/claude-sonnet-5"]
MODELS = [m for m in os.environ.get("EVAL_MODELS", "").split(",") if m] or DEFAULT_MODELS


def output_path_for(model: str) -> str:
    safe_name = model.replace("/", "_")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    return os.path.join(RESULTS_DIR, f"run_{safe_name}.jsonl")


def already_done(output_path: str) -> set[str]:
    if not os.path.exists(output_path):
        return set()
    done = set()
    with open(output_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                done.add(record["question_id"])
            except Exception:
                continue
    return done


def run_one(question: dict, model: str) -> dict:
    model_ref = f"openrouter:{model}"
    record = {
        "question_id": question["question_id"],
        "task": question["task"],
        "model": model,
        "query": question["query"],
    }
    try:
        with query_analyzer_agent.override(model=model_ref), \
             reasoning_agent.override(model=model_ref), \
             citation_verifier_agent.override(model=model_ref):
            result = run_pipeline(query=question["query"])
        record["schema_failure"] = False
        record["outcome"] = result.outcome
        record["citations_verified"] = result.citations_verified
        record["answer_text"] = " ".join(
            filter(None, [result.issue, result.rule, result.application, result.conclusion])
        )
        record["abstain_reason"] = result.abstain_reason
    except Exception as e:
        # Distinguish a schema/structured-output failure (model couldn't
        # comply) from a normal pipeline outcome -- do not conflate with
        # ABSTAIN, which is a legitimate architectural decision.
        record["schema_failure"] = True
        record["error"] = str(e)
        record["outcome"] = None
        record["citations_verified"] = False
        record["answer_text"] = ""
        record["abstain_reason"] = None
    return record


def run_for_model(questions: list[dict], model: str):
    output_path = output_path_for(model)
    done_ids = already_done(output_path)
    if done_ids:
        print(f"[{model}] Resuming — {len(done_ids)} questions already done, skipping those.")

    remaining = [q for q in questions if q["question_id"] not in done_ids]
    print(f"[{model}] Running {len(remaining)} questions...")

    start = time.time()
    for i, q in enumerate(remaining):
        record = run_one(q, model)
        with open(output_path, "a") as f:
            f.write(json.dumps(record) + "\n")
        elapsed = time.time() - start
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        status = "SCHEMA_FAILURE" if record["schema_failure"] else record["outcome"]
        print(f"  [{i+1}/{len(remaining)}] {q['question_id']}: {status} ({rate:.2f}/sec)")


def main():
    with open(QUESTIONS_PATH) as f:
        questions = json.load(f)
    print(f"Loaded {len(questions)} questions.")

    for model in MODELS:
        run_for_model(questions, model)

    print("\nAll models done. Run score_results.py next.")


if __name__ == "__main__":
    main()
