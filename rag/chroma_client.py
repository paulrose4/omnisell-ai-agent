from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from threading import RLock
from typing import Any

DEFAULT_COLLECTION = "omnisell_customer_kb"


class ChromaClientManager:
    _instances: dict[tuple[str | None, int | None, str | None], ChromaClientManager] = {}
    _instances_lock = RLock()

    def __new__(
        cls,
        host: str | None = None,
        port: int | None = None,
        persist_dir: str | Path | None = None,
    ) -> ChromaClientManager:
        key = (host, port, str(Path(persist_dir).resolve()) if persist_dir else None)
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = super().__new__(cls)
            return cls._instances[key]

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        persist_dir: str | Path | None = None,
    ) -> None:
        if getattr(self, "_initialized", False):
            return

        self.host = host or os.getenv("CHROMA_HOST")
        env_port = os.getenv("CHROMA_PORT")
        self.port = port or (int(env_port) if env_port else None)
        self.persist_dir = Path(persist_dir).resolve() if persist_dir else None
        self._client = self._create_client()
        self._collections: dict[str, Any] = {}
        self._lock = RLock()
        self._initialized = True

    def _create_client(self) -> Any:
        try:
            import chromadb
        except Exception as exc:
            raise RuntimeError(
                "chromadb is required. Install dependencies with `pip install -r requirements.txt`."
            ) from exc

        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            return chromadb.PersistentClient(path=str(self.persist_dir))

        if self.host and self.port:
            return chromadb.HttpClient(host=self.host, port=self.port)

        local_path = Path(__file__).resolve().parents[1] / "chroma_data"
        local_path.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(local_path))

    @property
    def client(self) -> Any:
        return self._client

    def get_collection(self, name: str = DEFAULT_COLLECTION) -> Any:
        with self._lock:
            if name not in self._collections:
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
            return self._collections[name]

    def reset_collection(self, name: str = DEFAULT_COLLECTION) -> Any:
        with self._lock:
            with suppress(Exception):
                self._client.delete_collection(name)
            self._collections.pop(name, None)
            return self.get_collection(name)

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        collection = self.get_collection(collection_name)
        collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int = 5,
        where: dict[str, Any] | None = None,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> dict[str, Any]:
        collection = self.get_collection(collection_name)
        kwargs: dict[str, Any] = {
            "query_embeddings": query_embeddings,
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        return collection.query(**kwargs)

    def count(self, collection_name: str = DEFAULT_COLLECTION) -> int:
        return int(self.get_collection(collection_name).count())

    def delete_ids(self, ids: list[str], collection_name: str = DEFAULT_COLLECTION) -> None:
        if not ids:
            return
        self.get_collection(collection_name).delete(ids=ids)

    def peek(self, limit: int = 5, collection_name: str = DEFAULT_COLLECTION) -> dict[str, Any]:
        return self.get_collection(collection_name).peek(limit=limit)


if __name__ == "__main__":
    manager = ChromaClientManager(persist_dir=Path(__file__).resolve().parents[1] / ".chroma_smoke")
    collection = manager.get_collection("omnisell_smoke_test")
    print(f"collection={collection.name}")
    print(f"count={manager.count('omnisell_smoke_test')}")
