from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi import File, Form, UploadFile
from pydantic import BaseModel, Field

from agents.tools.rag_tool import answer_customer_question
from agents.tools.vision_tool import answer_visual_customer_question
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


class CustomerImageAskRequest(CustomerAskRequest):
    image_base64: str = Field(..., min_length=1)
    task_hint: str | None = Field(default=None, max_length=120)


class CustomerImageAskResponse(CustomerAskResponse):
    vision: dict[str, Any]


class AsyncTaskSubmitResponse(BaseModel):
    task_id: str
    queue_name: str
    action: str
    status: str
    request_id: str | None = None


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


@router.post("/ask-async", response_model=AsyncTaskSubmitResponse)
async def ask_customer_question_async(payload: CustomerAskRequest, request: Request) -> AsyncTaskSubmitResponse:
    """Queue a text customer question and return a task id immediately."""
    record = request.app.state.task_queue.enqueue(
        queue_name="text",
        action="customer.ask",
        request_id=getattr(request.state, "request_id", None),
        handler=lambda: answer_customer_question(
            question=payload.question,
            target_language=payload.target_language,
            category_filter=payload.category_filter,
            top_k=payload.top_k,
        ),
    )
    return AsyncTaskSubmitResponse(
        task_id=record.task_id,
        queue_name=record.queue_name,
        action=record.action,
        status=record.status.value,
        request_id=record.request_id,
    )


def _image_result_to_response(result: dict[str, Any], request: Request) -> CustomerImageAskResponse:
    return CustomerImageAskResponse(
        agent=getattr(request.app.state.team.cs_agent, "name", "CustomerServiceAgent"),
        answer=str(result["answer"]),
        detected_language=str(result["detected_language"]),
        target_language=str(result["target_language"]),
        grounded=bool(result["grounded"]),
        context_used=list(result["context_used"]),
        vision=dict(result["vision"]),
    )


@router.post("/ask-image", response_model=CustomerImageAskResponse)
async def ask_customer_image_question(payload: CustomerImageAskRequest, request: Request) -> CustomerImageAskResponse:
    """Answer a customer question that includes a base64 image, using vision analysis plus RAG grounding."""
    result = answer_visual_customer_question(
        question=payload.question,
        image_base64=payload.image_base64,
        target_language=payload.target_language,
        category_filter=payload.category_filter,
        top_k=payload.top_k,
        task_hint=payload.task_hint,
    )
    return _image_result_to_response(result, request)


@router.post("/ask-image-upload", response_model=CustomerImageAskResponse)
async def ask_customer_image_upload(
    request: Request,
    image: UploadFile = File(...),
    question: str = Form(..., min_length=1, max_length=2000),
    target_language: str | None = Form(default="zh"),
    category_filter: str | None = Form(default=None),
    top_k: int = Form(default=3, ge=1, le=10),
    task_hint: str | None = Form(default=None, max_length=120),
) -> CustomerImageAskResponse:
    """Answer a customer question from an uploaded image, optimized for Swagger demos."""
    image_bytes = await image.read()
    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    result = answer_visual_customer_question(
        question=question,
        image_base64=image_base64,
        target_language=target_language,
        category_filter=category_filter,
        top_k=top_k,
        task_hint=task_hint,
    )
    return _image_result_to_response(result, request)


@router.post("/ask-image-async", response_model=AsyncTaskSubmitResponse)
async def ask_customer_image_async(payload: CustomerImageAskRequest, request: Request) -> AsyncTaskSubmitResponse:
    """Queue a base64 image customer question and return a task id immediately."""
    record = request.app.state.task_queue.enqueue(
        queue_name="vision",
        action="customer.ask_image",
        request_id=getattr(request.state, "request_id", None),
        handler=lambda: answer_visual_customer_question(
            question=payload.question,
            image_base64=payload.image_base64,
            target_language=payload.target_language,
            category_filter=payload.category_filter,
            top_k=payload.top_k,
            task_hint=payload.task_hint,
        ),
    )
    return AsyncTaskSubmitResponse(
        task_id=record.task_id,
        queue_name=record.queue_name,
        action=record.action,
        status=record.status.value,
        request_id=record.request_id,
    )


@router.post("/ask-image-upload-async", response_model=AsyncTaskSubmitResponse)
async def ask_customer_image_upload_async(
    request: Request,
    image: UploadFile = File(...),
    question: str = Form(..., min_length=1, max_length=2000),
    target_language: str | None = Form(default="zh"),
    category_filter: str | None = Form(default=None),
    top_k: int = Form(default=3, ge=1, le=10),
    task_hint: str | None = Form(default=None, max_length=120),
) -> AsyncTaskSubmitResponse:
    """Queue an uploaded image customer question and return a task id immediately."""
    image_bytes = await image.read()
    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    record = request.app.state.task_queue.enqueue(
        queue_name="vision",
        action="customer.ask_image_upload",
        request_id=getattr(request.state, "request_id", None),
        handler=lambda: answer_visual_customer_question(
            question=question,
            image_base64=image_base64,
            target_language=target_language,
            category_filter=category_filter,
            top_k=top_k,
            task_hint=task_hint,
        ),
    )
    return AsyncTaskSubmitResponse(
        task_id=record.task_id,
        queue_name=record.queue_name,
        action=record.action,
        status=record.status.value,
        request_id=record.request_id,
    )
