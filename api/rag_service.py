from __future__ import annotations

import time
from typing import Any

from agents.tools.rag_tool import search_knowledge_base


class RAGService:
    """Tenant-scoped service boundary around knowledge-base retrieval."""

    def search(self, query: str, top_k: int = 5, language: str | None = None, category_filter: str | None = None, source_type: str | None = None, tenant_id: str = "default") -> dict[str, Any]:
        started = time.perf_counter()
        results = search_knowledge_base(
            query=query, top_k=top_k, language=language,
            category_filter=category_filter, source_type=source_type, tenant_id=tenant_id,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "query": query, "top_k": top_k, "language": language,
            "category_filter": category_filter, "source_type": source_type,
            "tenant_id": tenant_id, "total": len(results), "latency_ms": latency_ms, "items": results,
        }
