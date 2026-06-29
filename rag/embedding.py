from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingLoadError(RuntimeError):
    """Raised when the configured embedding model cannot be loaded."""


@dataclass(slots=True)
class EmbeddingModel:
    model_name: str = DEFAULT_EMBEDDING_MODEL
    device: str | None = None
    normalize_embeddings: bool = True
    allow_fallback: bool = True
    force_fallback: bool = False
    fallback_dimension: int = 384
    _model: object | None = field(default=None, init=False, repr=False)
    _using_fallback: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._model = None
        self._using_fallback = False

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    def _load_model(self) -> None:
        if self._model is not None or self._using_fallback:
            return

        if self.force_fallback:
            self._using_fallback = True
            return

        try:
            from sentence_transformers import SentenceTransformer

            kwargs = {"device": self.device} if self.device else {}
            self._model = SentenceTransformer(self.model_name, **kwargs)
        except Exception as exc:
            if not self.allow_fallback:
                raise EmbeddingLoadError(f"Failed to load embedding model {self.model_name}: {exc}") from exc
            self._using_fallback = True

    def encode(self, texts: str | Iterable[str]) -> list[list[float]]:
        single_input = isinstance(texts, str)
        text_list = [texts] if single_input else [str(text) for text in texts]

        self._load_model()

        if self._using_fallback:
            vectors = [self._hash_embedding(text) for text in text_list]
        else:
            encoded = self._model.encode(  # type: ignore[union-attr]
                text_list,
                normalize_embeddings=self.normalize_embeddings,
                show_progress_bar=False,
            )
            vectors = [vector.tolist() for vector in encoded]

        return vectors

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]

    def _hash_embedding(self, text: str) -> list[float]:
        tokens = re.findall(r"[\w\u4e00-\u9fff\u0e00-\u0e7f]+", text.lower())
        if not tokens:
            tokens = [text.lower() or "empty"]

        vector = [0.0] * self.fallback_dimension
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for idx in range(0, len(digest), 4):
                bucket = int.from_bytes(digest[idx : idx + 4], "big") % self.fallback_dimension
                sign = 1.0 if digest[idx] % 2 == 0 else -1.0
                vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector


if __name__ == "__main__":
    embedder = EmbeddingModel(force_fallback=True)
    samples = [
        "订单多久发货？",
        "How long does shipping take?",
        "ฉันจะติดตามพัสดุได้อย่างไร",
    ]
    vectors = embedder.encode(samples)
    print(f"model={embedder.model_name}")
    print(f"using_fallback={embedder.using_fallback}")
    print(f"count={len(vectors)} dimension={len(vectors[0])}")
