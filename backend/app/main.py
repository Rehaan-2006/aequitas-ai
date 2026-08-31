from fastapi import FastAPI

app = FastAPI(title="Aequitas AI Backend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
