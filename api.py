"""HTTP API for the interview evaluation.

Exposes the evaluation report as an endpoint so the frontend (or you) can fetch a
scored report by user_id.

Run it:
  uv run uvicorn api:app --reload --port 8000

Then open:
  http://localhost:8000/docs                 # interactive Swagger UI
  http://localhost:8000/evaluation/127       # report for user_id 127
"""

from fastapi import FastAPI, HTTPException

from evaluation import build_report

app = FastAPI(title="VGI Interview Evaluation")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/evaluation/{user_id}")
def get_evaluation(user_id: str) -> dict:
    """Fetch the latest conversation for ``user_id``, score it, and return the report."""
    try:
        return build_report(user_id)
    except RuntimeError as exc:  # no conversation found
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"evaluation failed: {exc}")
