from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from agents.tools.rag_tool import answer_customer_question
from api.security import require_api_key


router = APIRouter(prefix="/api/customer", tags=["customer"], dependencies=[Depends(require_api_key)])


class CustomerAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    target_language: str | None = Field(default=None, min_length=2, max_length=8)
    category_filter: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)


class CustomerAskResponse(BaseModel):
    agent: str
    answer: str
    detected_language: str
    target_language: str
    grounded: bool
    context_used: list[dict[str, Any]]


@router.post("/ask", response_model=CustomerAskResponse)
async def ask_customer_question(payload: CustomerAskRequest, request: Request) -> CustomerAskResponse:
    """Answer a customer question with multilingual RAG grounding."""
    result = answer_customer_question(
        question=payload.question,
        target_language=payload.target_language,
        category_filter=payload.category_filter,
        top_k=payload.top_k,
    )
    return CustomerAskResponse(
        agent=getattr(request.app.state.team.cs_agent, "name", "CustomerServiceAgent"),
        answer=str(result["answer"]),
        detected_language=str(result["detected_language"]),
        target_language=str(result["target_language"]),
        grounded=bool(result["grounded"]),
        context_used=list(result["context_used"]),
    )
