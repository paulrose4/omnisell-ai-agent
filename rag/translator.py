from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


MODEL_MAP = {
    ("zh", "en"): "Helsinki-NLP/opus-mt-zh-en",
    ("en", "zh"): "Helsinki-NLP/opus-mt-en-zh",
    ("en", "th"): "Helsinki-NLP/opus-mt-en-th",
    ("en", "vi"): "Helsinki-NLP/opus-mt-en-vi",
    ("th", "en"): "Helsinki-NLP/opus-mt-th-en",
    ("vi", "en"): "Helsinki-NLP/opus-mt-vi-en",
    ("en", "mul"): "Helsinki-NLP/opus-mt-en-mul",
}


@dataclass
class Translator:
    device: int = -1
    allow_fallback: bool = True
    _pipelines: dict[str, Any] = field(default_factory=dict, init=False)

    def detect_language(self, text: str) -> str:
        if re.search(r"[\u0e00-\u0e7f]", text):
            return "th"
        if re.search(r"[\u4e00-\u9fff]", text):
            return "zh"
        if re.search(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", text, re.IGNORECASE):
            return "vi"
        return "en"

    def translate(self, text: str, target_language: str, source_language: str | None = None) -> str:
        source = source_language or self.detect_language(text)
        target = target_language
        if source == target:
            return text

        if (source, target) in MODEL_MAP:
            return self._translate_pair(text, source, target)

        if source != "en" and target != "en":
            english = self._translate_pair(text, source, "en")
            return self._translate_pair(english, "en", target)

        return self._translate_pair(text, source, target)

    def translate_query(self, query: str) -> str:
        source = self.detect_language(query)
        if source == "zh":
            return query
        if source == "en":
            return self.translate(query, target_language="zh", source_language="en")

        english = self.translate(query, target_language="en", source_language=source)
        return self.translate(english, target_language="zh", source_language="en")

    def translate_answer(self, answer: str, target_language: str, source_language: str = "zh") -> str:
        if target_language == source_language:
            return answer
        return self.translate(answer, target_language=target_language, source_language=source_language)

    def _translate_pair(self, text: str, source: str, target: str) -> str:
        model_name = MODEL_MAP.get((source, target))
        if model_name:
            try:
                return self._run_pipeline(model_name, text)
            except Exception as exc:
                if not self.allow_fallback:
                    raise RuntimeError(f"Translation failed for {source}->{target} using {model_name}: {exc}") from exc

        if source == "en" and target != "en":
            try:
                return self._run_pipeline(MODEL_MAP[("en", "mul")], text)
            except Exception as exc:
                if not self.allow_fallback:
                    raise RuntimeError(f"Fallback translation failed for en->mul: {exc}") from exc

        return text

    def _run_pipeline(self, model_name: str, text: str) -> str:
        if model_name not in self._pipelines:
            try:
                from transformers import pipeline
            except Exception as exc:
                raise RuntimeError("transformers is required for translation.") from exc

            self._pipelines[model_name] = pipeline(
                "translation",
                model=model_name,
                tokenizer=model_name,
                device=self.device,
            )

        result = self._pipelines[model_name](text, max_length=512)
        if isinstance(result, list) and result:
            return str(result[0].get("translation_text", text))
        return text


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    translator = Translator(allow_fallback=True)
    samples = ["订单多久发货？", "How do I return an item?", "ฉันจะคืนสินค้าได้อย่างไร", "Tôi có thể đổi trả hàng không?"]
    for sample in samples:
        language = translator.detect_language(sample)
        same_language = translator.translate(sample, target_language=language, source_language=language)
        print(f"{language}: {same_language}")
