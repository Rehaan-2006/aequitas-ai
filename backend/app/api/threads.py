"""
GET /threads, POST /threads/{id}/feedback, GET /threads/{id}/export endpoints.

Manages research thread history, user feedback, and document export with
the action gate (exports only approved documents).
"""

from typing import Literal
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/api", tags=["threads"])


class ThreadsResponse(BaseModel):
    id: str
    user_id: str
    query: str
    created_at: str
    feedback: int | None = None


class FeedbackRequest(BaseModel):
    feedback: Literal[1, -1]


class ExportResponse(BaseModel):
    id: str
    content: dict


@router.get("/threads", response_model=list[ThreadsResponse])
async def list_threads(
    user_id: str = Depends(get_current_user),
) -> list[ThreadsResponse]:
    """
    List all research threads for the current user.

    Returns only threads where user_id matches the requester.
    """
    client = get_supabase_client()

    try:
        response = client.table("research_threads").select(
            "id,user_id,query,created_at,feedback"
        ).eq("user_id", user_id).order("created_at", desc=True).execute()

        return [
            ThreadsResponse(
                id=thread["id"],
                user_id=thread["user_id"],
                query=thread["query"],
                created_at=thread["created_at"],
                feedback=thread.get("feedback"),
            )
            for thread in response.data
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch threads: {str(e)}",
        )


@router.post("/threads/{thread_id}/feedback")
async def set_feedback(
    thread_id: str,
    request: FeedbackRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    """
    Set feedback (thumbs up/down) on a research thread.

    Requires ownership of the thread. Feedback must be 1 or -1.

    Returns:
        {"status": "ok"}
    """
    client = get_supabase_client()

    # Fetch the thread and verify ownership
    try:
        thread_response = client.table("research_threads").select(
            "user_id"
        ).eq("id", thread_id).execute()

        if not thread_response.data or thread_response.data[0]["user_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch thread: {str(e)}",
        )

    # Update feedback
    try:
        client.table("research_threads").update(
            {"feedback": request.feedback}
        ).eq("id", thread_id).execute()

        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to set feedback: {str(e)}",
        )


@router.get("/threads/{thread_id}/export", response_model=ExportResponse)
async def export_thread(
    thread_id: str,
    user_id: str = Depends(get_current_user),
) -> ExportResponse:
    """
    Export a drafted document from a research thread.

    The Action Gate: only approved documents can be exported.

    Returns:
        {id, content_json} as structured JSON.
        PDF/DOCX rendering is deferred — see docs/DECISIONS.md.

    Raises:
        404: if no draft exists for this thread
        403: if the draft is not approved
    """
    client = get_supabase_client()

    # Fetch the thread and verify ownership
    try:
        thread_response = client.table("research_threads").select(
            "user_id"
        ).eq("id", thread_id).execute()

        if not thread_response.data or thread_response.data[0]["user_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread not found",
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch thread: {str(e)}",
        )

    # Fetch the associated draft (most recent)
    try:
        draft_response = client.table("legal_drafts").select(
            "id,content_json,approval_status"
        ).eq("thread_id", thread_id).order("updated_at", desc=True).limit(1).execute()

        if not draft_response.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No draft found for this thread",
            )

        draft = draft_response.data[0]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch draft: {str(e)}",
        )

    # Action Gate: enforce approval status
    if draft["approval_status"] != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Draft must be approved before export",
        )

    # Return the content as structured JSON.
    # PDF/DOCX rendering is deferred per scope cut documented in
    # docs/DECISIONS.md: /export returns structured JSON only, not rendered files.
    # File rendering will be added as a standalone task before Module 12 (Frontend).
    return ExportResponse(
        id=draft["id"],
        content=draft["content_json"],
    )
