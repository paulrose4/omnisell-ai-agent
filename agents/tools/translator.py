from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from rag.translator import Translator
except ImportError:
    from ...rag.translator import Translator  # type: ignore[no-redef]


@lru_cache(maxsize=1)
def get_translator() -> Translator:
    """Return a cached multilingual translator instance."""
    return Translator(allow_fallback=True)


def translate_text(text: str, target_language: str, source_language: str | None = None) -> str:
    """Translate text into target_language using the RAG OPUS-MT translation pipeline."""
    return get_translator().translate(text, target_language=target_language, source_language=source_language)


def detect_and_translate(text: str, target_language: str) -> dict[str, str]:
    """Detect the source language and translate text into target_language."""
    translator = get_translator()
    source_language = translator.detect_language(text)
    translated_text = translator.translate(text, target_language=target_language, source_language=source_language)
    return {
        "source_language": source_language,
        "target_language": target_language,
        "translated_text": translated_text,
    }


if __name__ == "__main__":
    sample = "订单多久发货？"
    print(detect_and_translate(sample, "zh"))
