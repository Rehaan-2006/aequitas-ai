"""
Aequitas AI -- append 25 NEW questions per task to eval_questions.json.

Samples only from cases not already used by the existing 75 questions, so
nothing overlaps. Uses the same question templates as build_eval_questions.py.
Backs up the old file first, and refuses to run twice (it expects exactly 75
existing questions).

Run from backend/:
    uv run python ../scripts/eval/add_eval_questions.py
"""

import json
import os
import random
import shutil

from build_eval_questions import fetch_candidate_cases, get_client

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "eval_questions.json")
N_NEW = 25

random.seed(7)

with open(PATH) as f:
    existing = json.load(f)
if len(existing) != 75:
    raise SystemExit(f"Expected 75 existing questions, found {len(existing)}. Not appending.")

shutil.copy(PATH, os.path.join(HERE, "eval_questions_75_backup.json"))
used = {q["case_id"] for q in existing}

client = get_client()
pool = [c for c in fetch_candidate_cases(client, "court") if c["id"] not in used]
if len(pool) < 3 * N_NEW:
    raise SystemExit(f"Only {len(pool)} unused cases available.")
random.shuffle(pool)

TASKS = {
    "court": (
        lambda c: f"What court decided the case {c['case_name']}, {c['citation']}?",
        lambda c: c["court"],
    ),
    "citation": (
        lambda c: f"What is the legal citation for the case {c['case_name']}?",
        lambda c: c["citation"],
    ),
    "quotation": (
        lambda c: (
            f"Provide a direct quotation from the court's opinion in "
            f"{c['case_name']}, {c['citation']}."
        ),
        lambda c: c["raw_text"],
    ),
}

new = []
for i, (task, (make_query, make_truth)) in enumerate(TASKS.items()):
    for c in pool[i * N_NEW : (i + 1) * N_NEW]:
        new.append(
            {
                "question_id": f"{task}_{c['id']}",
                "task": task,
                "case_id": c["id"],
                "case_name": c["case_name"],
                "citation": c["citation"],
                "query": make_query(c),
                "ground_truth": make_truth(c),
            }
        )

with open(PATH, "w") as f:
    json.dump(existing + new, f, indent=2)

print(f"Appended {len(new)} questions; eval_questions.json now has {len(existing) + len(new)}.")
