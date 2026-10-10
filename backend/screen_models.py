import importlib.util, json

spec = importlib.util.spec_from_file_location("rh", "../scripts/eval/run_harness.py")
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)

questions = json.load(open(rh.QUESTIONS_PATH))
sample = []
for task in ("court", "citation", "quotation"):
    sample += [q for q in questions if q["task"] == task][:2]

MODELS = [
    "openai/gpt-4o-mini", # "openai/gpt-3.5-turbo",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-v4.1-flash",
    "z-ai/glm-5.3-flash",
    "minimax/minimax-m3",
    "google/gemini-3.8-flash",
    "anthropic/claude-sonnet-5",
]

for m in MODELS:
    print(f"\n== {m}")
    for q in sample:
        r = rh.run_one(q, m)
        status = "SCHEMA_FAILURE" if r["schema_failure"] else str(r["outcome"]).split(".")[-1]
        why = (r.get("error") or r.get("abstain_reason") or "")[:90]
        print(f"  {q['task']:9s} {status:15s} {why}")