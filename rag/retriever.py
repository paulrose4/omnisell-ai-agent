from __future__ import annotations

import asyncio
from typing import Any

try:
    from .chroma_client import DEFAULT_COLLECTION, ChromaClientManager
    from .embedding import DEFAULT_EMBEDDING_MODEL, EmbeddingModel
except ImportError:
    from chroma_client import DEFAULT_COLLECTION, ChromaClientManager
    from embedding import DEFAULT_EMBEDDING_MODEL, EmbeddingModel


class Retriever:
    def __init__(
        self,
        collection_name: str = DEFAULT_COLLECTION,
        host: str | None = None,
        port: int | None = None,
        persist_dir: str | None = None,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        allow_embedding_fallback: bool = True,
        force_fallback_embedding: bool = False,
    ) -> None:
        self.collection_name = collection_name
        self.embedder = EmbeddingModel(
            model_name=model_name,
            allow_fallback=allow_embedding_fallback,
            force_fallback=force_fallback_embedding,
        )
        self.chroma = ChromaClientManager(host=host, port=port, persist_dir=persist_dir)
        self.chroma.get_collection(collection_name)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        language: str | None = None,
        category_filter: str | None = None,
        source_type: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.retrieve_with_score(
            query=query,
            top_k=top_k,
            language=language,
            category_filter=category_filter,
            source_type=source_type,
            tenant_id=tenant_id,
        )

    def retrieve_with_score(
        self,
        query: str,
        top_k: int = 5,
        language: str | None = None,
        category_filter: str | None = None,
        source_type: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where = self._build_where(
            language=language, category_filter=category_filter, source_type=source_type, tenant_id=tenant_id
        )
        query_embedding = self.embedder.encode_one(query)
        result = self.chroma.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            collection_name=self.collection_name,
        )
        return self._format_results(result)

    async def aretrieve(
        self,
        query: str,
        top_k: int = 5,
        language: str | None = None,
        category_filter: str | None = None,
        source_type: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.aretrieve_with_score(
            query=query,
            top_k=top_k,
            language=language,
            category_filter=category_filter,
            source_type=source_type,
            tenant_id=tenant_id,
        )

    async def aretrieve_with_score(
        self,
        query: str,
        top_k: int = 5,
        language: str | None = None,
        category_filter: str | None = None,
        source_type: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.retrieve_with_score,
            query,
            top_k,
            language,
            category_filter,
            source_type,
            tenant_id,
        )

    def _build_where(
        self,
        language: str | None = None,
        category_filter: str | None = None,
        source_type: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        filters: list[dict[str, Any]] = []
        if tenant_id:
            filters.append({"tenant_id": tenant_id})
        if language:
            filters.append({"language": language})
        if category_filter:
            filters.append({"category": category_filter})
        if source_type:
            filters.append({"source_type": source_type})

        if not filters:
            return None
        if len(filters) == 1:
            return filters[0]
        return {"$and": filters}

    def _format_results(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        documents = result.get("documents", [[]])[0] or []
        metadatas = result.get("metadatas", [[]])[0] or []
        distances = result.get("distances", [[]])[0] or []

        formatted = []
        for document, metadata, distance in zip(documents, metadatas, distances, strict=True):
            similarity = 1.0 - float(distance)
            formatted.append(
                {
                    "text": document,
                    "metadata": metadata or {},
                    "score": similarity,
                    "distance": float(distance),
                }
            )
        return formatted


if __name__ == "__main__":
    import sys
    import tempfile

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        from .build_kb import build_knowledge_base
    except ImportError:
        from build_kb import build_knowledge_base

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        build_knowledge_base(
            persist_dir=temp_dir,
            reset=True,
            batch_size=32,
            force_fallback_embedding=True,
        )
        retriever = Retriever(persist_dir=temp_dir, force_fallback_embedding=True)
        results = retriever.retrieve("订单多久可以发货？", top_k=3, language="zh", category_filter="物流")
        print(f"results={len(results)}")
        for item in results:
            print(f"score={item['score']:.4f} category={item['metadata'].get('category')} text={item['text'][:80]}")
