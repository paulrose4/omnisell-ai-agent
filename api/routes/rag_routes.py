from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.security import require_api_key

router = APIRouter(prefix="/api/rag", tags=["rag"], dependencies=[Depends(require_api_key)])


class RAGSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    language: str | None = Field(default=None, min_length=2, max_length=8)
    category_filter: str | None = None
    source_type: str | None = None


@router.post("/search")
async def search_rag(payload: RAGSearchRequest, request: Request) -> dict[str, Any]:
    """Search the RAG knowledge base through the API service boundary."""
    return request.app.state.rag_service.search(
        query=payload.query,
        top_k=payload.top_k,
        language=payload.language,
        category_filter=payload.category_filter,
        source_type=payload.source_type,
    )
