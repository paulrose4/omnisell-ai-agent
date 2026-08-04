from __future__ import annotations

from types import SimpleNamespace

from rag.retriever import Retriever


def test_retriever_applies_tenant_filter_to_vector_query() -> None:
    captured: dict = {}

    class Chroma:
        @staticmethod
        def query(**kwargs):
            captured.update(kwargs)
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    retriever = object.__new__(Retriever)
    retriever.embedder = SimpleNamespace(encode_one=lambda _query: [0.1, 0.2])
    retriever.chroma = Chroma()
    retriever.collection_name = "test-kb"

    result = retriever.retrieve_with_score(
        query="refund policy",
        tenant_id="tenant-a",
        language="en",
        category_filter="returns",
    )

    assert result == []
    assert captured["where"] == {
        "$and": [
            {"tenant_id": "tenant-a"},
            {"language": "en"},
            {"category": "returns"},
        ]
    }
