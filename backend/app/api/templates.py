"""
GET /templates endpoint — lists available document templates for the
Drafting Agent (Module 8). Read-only, service-role lookup, same pattern
as the other routers in this package.
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/api", tags=["templates"])


class TemplateResponse(BaseModel):
    id: str
    title: str
    jurisdiction: str
    category: str
    structure_schema: dict


@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(
    user_id: str = Depends(get_current_user),
) -> list[TemplateResponse]:
    """List all document templates available to the Drafting Agent."""
    client = get_supabase_client()

    try:
        response = client.table("document_templates").select(
            "id,title,jurisdiction,category,structure_schema"
        ).execute()

        return [TemplateResponse(**row) for row in response.data]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch templates: {str(e)}",
        )
