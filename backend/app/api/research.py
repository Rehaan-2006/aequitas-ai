"""
POST /research endpoint for running the complete research pipeline.

Handles authentication, credit deduction, pipeline execution,
and result persistence.
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.config import settings
from app.db.supabase_client import get_supabase_client
from app.services.pipeline import run_pipeline, PipelineOutcome, PipelineResult
from app.services.credits import ensure_credits_row, check_and_deduct_credits, add_credit

router = APIRouter(prefix="/api", tags=["research"])


class ResearchRequest(BaseModel):
    query: str
    jurisdiction: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class ResearchResponse(BaseModel):
    thread_id: str
    result: PipelineResult


@router.post("/research", response_model=ResearchResponse)
def research(
    request: ResearchRequest,
    user_id: str = Depends(get_current_user),
) -> ResearchResponse:
    """
    Run the complete research pipeline on a user query.

    Requires valid JWT. Deducts research_credit_cost from the user's balance.
    If sanitization rejects the query, refunds the credit.

    Returns:
        thread_id (the saved research_threads row) and the full pipeline result
    """
    # Ensure user has a credits row
    ensure_credits_row(user_id)

    # Check and deduct credits atomically
    if not check_and_deduct_credits(user_id, settings.research_credit_cost):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits for research",
        )

    # Run the pipeline
    try:
        result = run_pipeline(
            query=request.query,
            jurisdiction=request.jurisdiction,
            date_from=request.date_from,
            date_to=request.date_to,
        )
    except Exception as e:
        # Unexpected error — refund the credit
        add_credit(user_id, settings.research_credit_cost)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline execution failed: {str(e)}",
        )

    # If sanitization rejected the query, refund the credit
    # (no real pipeline work happened)
    if result.outcome == PipelineOutcome.REJECTED_AT_SANITIZATION:
        add_credit(user_id, settings.research_credit_cost)

    # Persist the research thread
    client = get_supabase_client()
    try:
        thread_response = client.table("research_threads").insert(
            {
                "user_id": user_id,
                "query": request.query,
                "result_json": result.model_dump(),
                "trace_json": getattr(result, "_trace", None) or {},
            }
        ).execute()

        thread_id = thread_response.data[0]["id"]

        return ResearchResponse(
            thread_id=thread_id,
            result=result,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save research thread: {str(e)}",
        )
