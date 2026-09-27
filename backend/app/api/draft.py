"""
POST /draft endpoint for generating legal documents from research threads.

Includes approval/rejection endpoints and re-verification after drafting.
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.config import settings
from app.db.supabase_client import get_supabase_client
from app.services.drafting import draft_document, DraftResult
from app.services.credits import ensure_credits_row, check_and_deduct_credits

router = APIRouter(prefix="/api", tags=["draft"])


class DraftRequest(BaseModel):
    thread_id: str
    template_id: str


class DraftResponse(BaseModel):
    draft_id: str
    draft: DraftResult


@router.post("/draft", response_model=DraftResponse)
async def create_draft(
    request: DraftRequest,
    user_id: str = Depends(get_current_user),
) -> DraftResponse:
    """
    Generate a legal document from a completed research thread.

    Requires valid JWT and ownership of the thread. Deducts draft_credit_cost
    from the user's balance.

    Returns:
        draft_id (the saved legal_drafts row) and the full draft result
    """
    client = get_supabase_client()

    # Fetch the research thread, verify ownership
    try:
        thread_response = client.table("research_threads").select(
            "id,user_id,result_json,trace_json"
        ).eq("id", request.thread_id).execute()

        if not thread_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Research thread not found",
            )

        thread = thread_response.data[0]
        if thread["user_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Research thread not found",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch research thread: {str(e)}",
        )

    # Ensure user has a credits row and check balance
    ensure_credits_row(user_id)
    if not check_and_deduct_credits(user_id, settings.draft_credit_cost):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits for drafting",
        )

    # Extract verified chunks and IRAC result from stored research
    result_json = thread["result_json"]
    trace_json = thread.get("trace_json", {})

    # Reconstruct verified_chunks from trace (Module 8 spec)
    # The trace contains PipelineTraceEntry records with the chunks at each stage
    verified_chunks = []
    if trace_json and "trace" in trace_json:
        for entry in trace_json["trace"]:
            if entry.get("stage") == "citation_verification" and entry.get("data"):
                # Extract chunks from the verification stage
                verified_chunks = entry["data"].get("valid_chunks", [])
                break

    # Draft the document
    try:
        draft_result = draft_document(
            query=thread.get("query", ""),
            irac_result=result_json,
            verified_chunks=verified_chunks,
            template_id=request.template_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Drafting failed: {str(e)}",
        )

    # Persist the draft
    try:
        draft_response = client.table("legal_drafts").insert(
            {
                "thread_id": request.thread_id,
                "template_id": request.template_id,
                "content_json": draft_result.model_dump(),
                "verification_status": draft_result.verification_status,
                "approval_status": "pending_review",
            }
        ).execute()

        draft_id = draft_response.data[0]["id"]

        return DraftResponse(
            draft_id=draft_id,
            draft=draft_result,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save draft: {str(e)}",
        )


@router.post("/draft/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    user_id: str = Depends(get_current_user),
) -> dict:
    """
    Approve a draft for export (set approval_status='approved').

    Requires ownership of the associated thread.
    """
    client = get_supabase_client()

    # Fetch the draft and verify ownership (via the thread)
    try:
        draft_response = client.table("legal_drafts").select(
            "id,thread_id"
        ).eq("id", draft_id).execute()

        if not draft_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft not found",
            )

        draft = draft_response.data[0]

        # Verify ownership via the associated thread
        thread_response = client.table("research_threads").select(
            "user_id"
        ).eq("id", draft["thread_id"]).execute()

        if not thread_response.data or thread_response.data[0]["user_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft not found",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch draft: {str(e)}",
        )

    # Update approval status
    try:
        client.table("legal_drafts").update(
            {"approval_status": "approved"}
        ).eq("id", draft_id).execute()

        return {"status": "approved"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve draft: {str(e)}",
        )


@router.post("/draft/{draft_id}/reject")
async def reject_draft(
    draft_id: str,
    user_id: str = Depends(get_current_user),
) -> dict:
    """
    Reject a draft (set approval_status='rejected').

    Requires ownership of the associated thread.
    """
    client = get_supabase_client()

    # Fetch the draft and verify ownership (via the thread)
    try:
        draft_response = client.table("legal_drafts").select(
            "id,thread_id"
        ).eq("id", draft_id).execute()

        if not draft_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft not found",
            )

        draft = draft_response.data[0]

        # Verify ownership via the associated thread
        thread_response = client.table("research_threads").select(
            "user_id"
        ).eq("id", draft["thread_id"]).execute()

        if not thread_response.data or thread_response.data[0]["user_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft not found",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch draft: {str(e)}",
        )

    # Update approval status
    try:
        client.table("legal_drafts").update(
            {"approval_status": "rejected"}
        ).eq("id", draft_id).execute()

        return {"status": "rejected"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject draft: {str(e)}",
        )
