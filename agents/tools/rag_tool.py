from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from rag.retriever import Retriever
except ImportError:
    from ...rag.retriever import Retriever  # type: ignore[no-redef]

try:
    from .translator import get_translator
except ImportError:
    from translator import get_translator  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAG_DATA_DIR = PROJECT_ROOT / "rag" / "data"


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    """Return a cached Chroma-backed RAG retriever."""
    return Retriever()


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]|[\u0e00-\u0e7f]+", text.lower()))
    if len(text) >= 2:
        tokens.update(text[index : index + 2] for index in range(len(text) - 1) if not text[index : index + 2].isspace())
    return {token for token in tokens if token.strip()}


def _load_faq_files() -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for language, filename in {"zh": "faq_cn.json", "en": "faq_en.json", "th": "faq_th.json", "vi": "faq_vi.json"}.items():
        path = RAG_DATA_DIR / filename
        if not path.exists():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            rows.append((language, item))
    return rows


def _fallback_search(
    query: str,
    top_k: int,
    language: str | None = None,
    category_filter: str | None = None,
) -> list[dict[str, Any]]:
    query_tokens = _tokenize(query)
    scored: list[dict[str, Any]] = []

    for item_language, item in _load_faq_files():
        if language and item_language != language:
            continue
        if category_filter and item.get("category") != category_filter and category_filter not in item.get("tags", []):
            continue

        haystack = " ".join(
            [
                str(item.get("question", "")),
                str(item.get("answer", "")),
                str(item.get("category", "")),
                " ".join(str(tag) for tag in item.get("tags", [])),
                json.dumps(item.get("languages", {}), ensure_ascii=False),
            ]
        )
        haystack_tokens = _tokenize(haystack)
        overlap = len(query_tokens & haystack_tokens)
        if overlap == 0 and query.lower() not in haystack.lower():
            continue

        score = overlap / max(1, len(query_tokens))
        text = f"Question: {item.get('question', '')}\nAnswer: {item.get('answer', '')}"
        scored.append(
            {
                "text": text,
                "metadata": {
                    "source_type": "faq",
                    "language": item_language,
                    "category": item.get("category", ""),
                    "tags": json.dumps(item.get("tags", []), ensure_ascii=False),
                    "product_id": "",
                    "source_id": item.get("id", ""),
                },
                "score": round(score, 4),
                "distance": round(1.0 - score, 4),
            }
        )

    return sorted(scored, key=lambda row: row["score"], reverse=True)[:top_k]


def search_knowledge_base(
    query: str,
    top_k: int = 5,
    language: str | None = None,
    category_filter: str | None = None,
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search the OmniSell RAG knowledge base and return text, metadata, and similarity score."""
    try:
        results = get_retriever().retrieve_with_score(
            query=query,
            top_k=top_k,
            language=language,
            category_filter=category_filter,
            source_type=source_type,
        )
        if results:
            return results
    except Exception:
        pass

    if source_type and source_type != "faq":
        return []
    return _fallback_search(query=query, top_k=top_k, language=language, category_filter=category_filter)


def answer_customer_question(
    question: str,
    target_language: str | None = None,
    category_filter: str | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """Answer a customer question using RAG context only, with multilingual query and answer handling."""
    translator = get_translator()
    detected_language = translator.detect_language(question)
    response_language = target_language or detected_language

    try:
        normalized_query = translator.translate_query(question)
    except Exception:
        normalized_query = question

    results = search_knowledge_base(
        query=normalized_query,
        top_k=top_k,
        language="zh",
        category_filter=category_filter,
        source_type="faq",
    )

    if not results:
        zh_answer = "抱歉，我没有在知识库中找到足够可靠的信息。请提供订单号、商品链接或更多细节，我会转交人工客服核实。"
    else:
        best = results[0]
        text = str(best["text"])
        match = re.search(r"Answer:\s*(.+)", text, flags=re.DOTALL)
        raw_answer = match.group(1).strip() if match else text.strip()
        zh_answer = f"{raw_answer}\n\n以上回答基于当前客服知识库。如涉及具体订单，请以订单详情和人工审核结果为准。"

    try:
        final_answer = translator.translate_answer(zh_answer, target_language=response_language, source_language="zh")
    except Exception:
        final_answer = zh_answer

    return {
        "question": question,
        "detected_language": detected_language,
        "target_language": response_language,
        "normalized_query": normalized_query,
        "answer": final_answer,
        "context_used": results,
        "grounded": bool(results),
    }


if __name__ == "__main__":
    print(answer_customer_question("如何申请退货？", target_language="zh", category_filter="退换货"))
