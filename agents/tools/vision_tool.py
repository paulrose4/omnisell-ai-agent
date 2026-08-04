from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

try:
    from .rag_tool import answer_customer_question
except ImportError:
    from rag_tool import answer_customer_question  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_VISION_BASE_URL = "http://localhost:11434"
DEFAULT_VISION_MODEL_NAME = "llava:7b"
DEFAULT_VISION_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_TEXT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_TEXT_MODEL_NAME = "qwen2.5:3b"

_DATA_URL_PREFIX = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)


def _ollama_base_url(base_url: str | None = None) -> str:
    resolved = (
        (
            base_url
            or os.getenv("VISION_BASE_URL")
            or os.getenv("OLLAMA_BASE_URL")
            or os.getenv("MODEL_BASE_URL")
            or os.getenv("VLLM_BASE_URL")
            or DEFAULT_VISION_BASE_URL
        )
        .strip()
        .rstrip("/")
    )
    if resolved.endswith("/v1"):
        resolved = resolved[:-3]
    return resolved


def _vision_model_name(model_name: str | None = None) -> str:
    return (model_name or os.getenv("VISION_MODEL_NAME") or DEFAULT_VISION_MODEL_NAME).strip()


def _vision_timeout(timeout_seconds: float | None = None) -> float:
    if timeout_seconds is not None:
        return timeout_seconds
    raw_value = os.getenv("VISION_TIMEOUT_SECONDS")
    if not raw_value:
        return DEFAULT_VISION_TIMEOUT_SECONDS
    try:
        return float(raw_value)
    except ValueError:
        return DEFAULT_VISION_TIMEOUT_SECONDS


def _max_image_bytes(max_image_bytes: int | None = None) -> int:
    if max_image_bytes is not None:
        return max_image_bytes
    raw_value = os.getenv("VISION_MAX_IMAGE_BYTES")
    if not raw_value:
        return DEFAULT_MAX_IMAGE_BYTES
    try:
        return int(raw_value)
    except ValueError:
        return DEFAULT_MAX_IMAGE_BYTES


def _text_base_url() -> str:
    return (os.getenv("MODEL_BASE_URL") or os.getenv("VLLM_BASE_URL") or DEFAULT_TEXT_BASE_URL).strip()


def _text_model_name() -> str:
    return (os.getenv("MODEL_NAME") or os.getenv("VLLM_MODEL_NAME") or DEFAULT_TEXT_MODEL_NAME).strip()


def normalize_image_base64(image_base64: str, max_image_bytes: int | None = None) -> str:
    """Return a clean base64 image string suitable for Ollama's native image API."""
    cleaned = _DATA_URL_PREFIX.sub("", image_base64.strip(), count=1)
    cleaned = "".join(cleaned.split())
    if not cleaned:
        raise ValueError("image_base64 cannot be empty.")

    try:
        image_bytes = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image_base64 must be valid base64 image content.") from exc

    max_bytes = _max_image_bytes(max_image_bytes)
    if len(image_bytes) > max_bytes:
        raise ValueError(f"Image is too large: {len(image_bytes)} bytes exceeds {max_bytes} bytes.")

    return base64.b64encode(image_bytes).decode("ascii")


def image_file_to_base64(image_path: str | Path, max_image_bytes: int | None = None) -> str:
    """Read an image file and encode it for the vision model."""
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path}")

    image_bytes = path.read_bytes()
    max_bytes = _max_image_bytes(max_image_bytes)
    if len(image_bytes) > max_bytes:
        raise ValueError(f"Image is too large: {len(image_bytes)} bytes exceeds {max_bytes} bytes.")
    if not image_bytes:
        raise ValueError("Image file cannot be empty.")

    return base64.b64encode(image_bytes).decode("ascii")


def _build_vision_prompt(question: str, target_language: str | None = None, task_hint: str | None = None) -> str:
    response_language = target_language or "zh"
    task_line = f"\nTask hint: {task_hint}" if task_hint else ""
    return f"""You are OmniSell's ecommerce multimodal vision analyst.
Only use visible information from the image. Do not infer facts that are not visible.{task_line}

Customer question:
{question}

Required final language code: {response_language}

Return a concise structured analysis with these fields:
1. Scene type: product image, order screenshot, after-sales evidence, competitor price screenshot, logistics screenshot, or other.
2. Exact visible text/OCR: transcribe readable text exactly. If a character is unclear, mark it as [unclear] instead of guessing.
3. Visible facts: product type, color, style, brand/model, order number, amount, price, damage area, logistics status, and any readable text.
4. Customer-service keywords: terms that are useful for RAG retrieval and human review.
5. Risk notes: what cannot be confirmed from the image alone and needs order details or human audit.

For screenshots, prioritize exact OCR over interpretation. Keep the output objective and suitable for ecommerce support workflows."""


def _post_ollama_generate(base_url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    url = f"{base_url}/api/generate"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Vision model request failed: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Vision model returned an invalid response.")
    return data


def analyze_customer_image(
    question: str,
    image_base64: str | None = None,
    image_path: str | Path | None = None,
    target_language: str | None = "zh",
    task_hint: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_image_bytes: int | None = None,
) -> dict[str, Any]:
    """Analyze a customer image with the local Ollama vision model."""
    if bool(image_base64) == bool(image_path):
        raise ValueError("Provide exactly one of image_base64 or image_path.")

    encoded_image = (
        normalize_image_base64(image_base64 or "", max_image_bytes=max_image_bytes)
        if image_base64
        else image_file_to_base64(image_path or "", max_image_bytes=max_image_bytes)
    )
    resolved_base_url = _ollama_base_url(base_url)
    resolved_model_name = _vision_model_name(model_name)
    resolved_timeout = _vision_timeout(timeout_seconds)

    payload = {
        "model": resolved_model_name,
        "prompt": _build_vision_prompt(question, target_language=target_language, task_hint=task_hint),
        "images": [encoded_image],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
        },
    }
    data = _post_ollama_generate(resolved_base_url, payload, resolved_timeout)
    analysis = str(data.get("response", "")).strip()
    if not analysis:
        raise RuntimeError("Vision model returned an empty analysis.")

    return {
        "question": question,
        "vision_model": resolved_model_name,
        "vision_base_url": resolved_base_url,
        "analysis": analysis,
        "task_hint": task_hint or "auto",
    }


def _synthesis_enabled() -> bool:
    return os.getenv("MULTIMODAL_SYNTHESIS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def synthesize_visual_customer_answer(
    question: str,
    vision_analysis: str,
    rag_answer: str,
    grounded: bool,
    target_language: str | None = "zh",
) -> str:
    """Use the text model to compose a customer-facing answer from vision and RAG evidence."""
    if not _synthesis_enabled():
        return ""

    prompt = f"""Create a customer-facing ecommerce support reply from the evidence below.
Rules:
- Use only the image analysis and knowledge-base answer.
- Do not invent order status, refund results, compensation, delivery status, or platform decisions.
- If human review is needed, say so clearly.
- Start with a direct conclusion, then list next steps.
- Reply in this target language code: {target_language or "zh"}.

Customer question:
{question}

Image analysis:
{vision_analysis}

Knowledge base matched: {grounded}
Knowledge-base answer:
{rag_answer or "No sufficient knowledge-base evidence was found."}
"""
    try:
        client = OpenAI(
            base_url=_text_base_url(),
            api_key=os.getenv("MODEL_API_KEY") or os.getenv("VLLM_API_KEY", "not-needed"),
            timeout=90.0,
        )
        response = client.chat.completions.create(
            model=_text_model_name(),
            messages=[
                {"role": "system", "content": "You are a policy-grounded OmniSell ecommerce support lead."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        return (response.choices[0].message.content or "").strip()
    except (OpenAIError, Exception):
        return ""


def answer_visual_customer_question(
    question: str,
    image_base64: str | None = None,
    image_path: str | Path | None = None,
    target_language: str | None = "zh",
    category_filter: str | None = None,
    top_k: int = 3,
    task_hint: str | None = None,
) -> dict[str, Any]:
    """Answer an image-based customer question by combining vision analysis with RAG grounding."""
    vision = analyze_customer_image(
        question=question,
        image_base64=image_base64,
        image_path=image_path,
        target_language=target_language,
        task_hint=task_hint,
    )
    vision_analysis = str(vision["analysis"])
    grounded_question = f"{question}\n\nImage analysis:\n{vision_analysis}\n\nUse the image analysis to handle this ecommerce support question."
    rag_result = answer_customer_question(
        question=grounded_question,
        target_language=target_language,
        category_filter=category_filter,
        top_k=top_k,
    )

    rag_answer = str(rag_result.get("answer", "")).strip()
    grounded = bool(rag_result.get("grounded"))
    synthesized_answer = synthesize_visual_customer_answer(
        question=question,
        vision_analysis=vision_analysis,
        rag_answer=rag_answer,
        grounded=grounded,
        target_language=target_language,
    )
    if synthesized_answer:
        final_answer = synthesized_answer
    elif rag_answer:
        final_answer = f"Image analysis:\n{vision_analysis}\n\nSupport recommendation:\n{rag_answer}"
    else:
        final_answer = (
            f"Image analysis:\n{vision_analysis}\n\n"
            "The current knowledge base does not provide enough evidence. Please provide the order number, product link, or additional screenshots for human review."
        )

    return {
        "question": question,
        "detected_language": str(rag_result.get("detected_language", target_language or "zh")),
        "target_language": str(rag_result.get("target_language", target_language or "zh")),
        "normalized_query": str(rag_result.get("normalized_query", grounded_question)),
        "answer": final_answer,
        "context_used": list(rag_result.get("context_used", [])),
        "grounded": grounded,
        "vision": vision,
    }
