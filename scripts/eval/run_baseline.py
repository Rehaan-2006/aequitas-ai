"""
Aequitas AI -- raw-LLM baseline for the eval harness.

Same questions and models as run_harness.py, but each model answers the bare
question with no retrieval, reranking, validity check, or citation
verification (one call per question). Records use the same format as
run_harness.py, so score_results.py scores them unchanged. Files are named
run_<model>_baseline.jsonl.

Run from backend/:
    BASELINE_MODELS="google/gemini-3.8-flash,openai/gpt-4o-mini" \
        uv run python ../scripts/eval/run_baseline.py
Optional: WORKERS=4 (parallel requests per model).

Calls that error or return empty text are NOT written, so a rerun retries them.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.getcwd())

from app.core.config import settings  # noqa: E402

os.environ.setdefault("OPENROUTER_API_KEY", settings.llm_provider_api_key)

from pydantic_ai import Agent  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_PATH = os.path.join(HERE, "eval_questions.json")
RESULTS_DIR = os.path.join(HERE, "results")
MODELS = [m.strip() for m in os.environ.get("BASELINE_MODELS", "").split(",") if m.strip()]
WORKERS = int(os.environ.get("WORKERS", "4"))
MAX_ERRORS = 8


def output_path(model: str) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    return os.path.join(RESULTS_DIR, f"run_{model.replace('/', '_')}_baseline.jsonl")


def done_ids(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    ids = set()
    with open(path) as f:
        for line in f:
            if line.strip():
                try:
                    ids.add(json.loads(line)["question_id"])
                except Exception:
                    pass
    return ids


async def run_model(model: str, questions: list[dict]) -> None:
    path = output_path(model)
    done = done_ids(path)
    todo = [q for q in questions if q["question_id"] not in done]
    print(f"[{model}] baseline: {len(todo)} to run ({len(done)} already done)")
    if not todo:
        return

    agent = Agent(f"openrouter:{model}", defer_model_check=True)
    sem = asyncio.Semaphore(WORKERS)
    errors: list[str] = []
    written = 0

    async def work(q: dict) -> None:
        nonlocal written
        if len(errors) >= MAX_ERRORS:
            return
        async with sem:
            if len(errors) >= MAX_ERRORS:
                return
            try:
                result = await agent.run(q["query"])
                text = (result.output or "").strip()
                if not text:
                    raise ValueError("empty response")
            except Exception as e:
                errors.append(str(e)[:150])
                print(f"  [{model}] {q['question_id'][:18]} ERROR: {errors[-1]}")
                return
        record = {
            "question_id": q["question_id"],
            "task": q["task"],
            "model": model,
            "arm": "baseline",
            "query": q["query"],
            "schema_failure": False,
            "outcome": "answered",
            "citations_verified": False,
            "answer_text": text,
            "abstain_reason": None,
        }
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
        written += 1
        if written % 10 == 0:
            print(f"  [{model}] {written}/{len(todo)}")

    await asyncio.gather(*(work(q) for q in todo))
    print(f"[{model}] done: {written} written, {len(errors)} errors")
    if len(errors) >= MAX_ERRORS:
        print(f"[{model}] stopped early after {MAX_ERRORS} errors; rerun to retry the rest")


def main() -> None:
    if not MODELS:
        raise SystemExit("Set BASELINE_MODELS to a comma-separated list of model ids.")
    with open(QUESTIONS_PATH) as f:
        questions = json.load(f)
    print(f"Loaded {len(questions)} questions.")
    for model in MODELS:
        asyncio.run(run_model(model, questions))


if __name__ == "__main__":
    main()
