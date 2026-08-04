from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .chroma_client import DEFAULT_COLLECTION, ChromaClientManager
    from .embedding import DEFAULT_EMBEDDING_MODEL, EmbeddingModel
except ImportError:
    from chroma_client import DEFAULT_COLLECTION, ChromaClientManager
    from embedding import DEFAULT_EMBEDDING_MODEL, EmbeddingModel


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

FAQ_FILES = {
    "zh": "faq_cn.json",
    "en": "faq_en.json",
    "th": "faq_th.json",
    "vi": "faq_vi.json",
}


@dataclass
class BuildStats:
    faq_documents: int = 0
    product_documents: int = 0
    policy_documents: int = 0
    total_documents: int = 0
    source_files: list[str] = field(default_factory=list)
    collection_count: int = 0
    used_fallback_embedding: bool = False


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing RAG data file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


def metadata(
    source_type: str,
    language: str,
    category: str,
    tags: Iterable[str] | None = None,
    product_id: str = "",
    source_id: str = "",
) -> dict[str, str]:
    return {
        "source_type": source_type,
        "language": language,
        "category": category,
        "tags": json.dumps(list(tags or []), ensure_ascii=False),
        "product_id": product_id,
        "source_id": source_id,
    }


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(0, end - chunk_overlap)
    return [chunk for chunk in chunks if chunk]


def faq_to_document(item: dict[str, Any], language: str) -> str:
    languages = item.get("languages", {})
    translations = []
    for lang_key in ("en", "th", "vi"):
        translation = languages.get(lang_key)
        if isinstance(translation, dict):
            translations.append(
                f"{lang_key.upper()} Q: {translation.get('question', '')}\n{lang_key.upper()} A: {translation.get('answer', '')}"
            )

    return "\n".join(
        [
            f"FAQ ID: {item.get('id', '')}",
            f"Language: {language}",
            f"Category: {item.get('category', '')}",
            f"Question: {item.get('question', '')}",
            f"Answer: {item.get('answer', '')}",
            *translations,
        ]
    )


def product_to_text(product: dict[str, Any]) -> str:
    specs = product.get("specs", {})
    spec_text = "\n".join(f"- {key}: {value}" for key, value in specs.items())
    return "\n".join(
        [
            f"Product ID: {product.get('product_id', '')}",
            f"Category: {product.get('category', '')}",
            f"Title zh: {product.get('title_zh', '')}",
            f"Title en: {product.get('title_en', '')}",
            f"Description zh: {product.get('description_zh', '')}",
            f"Description en: {product.get('description_en', '')}",
            f"Material zh: {product.get('material_zh', '')}",
            f"Material en: {product.get('material_en', '')}",
            f"Sizes: {', '.join(product.get('sizes', []))}",
            f"Care zh: {product.get('care_zh', '')}",
            f"Care en: {product.get('care_en', '')}",
            f"Specs:\n{spec_text}",
        ]
    )


def prepare_documents(data_dir: Path = DATA_DIR) -> tuple[list[str], list[str], list[dict[str, str]], BuildStats]:
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []
    stats = BuildStats()

    for language, filename in FAQ_FILES.items():
        path = data_dir / filename
        faq_items = load_json(path)
        stats.source_files.append(str(path))
        for item in faq_items:
            doc_id = f"faq:{language}:{item['id']}"
            ids.append(doc_id)
            documents.append(faq_to_document(item, language))
            metadatas.append(
                metadata(
                    source_type="faq",
                    language=language,
                    category=str(item.get("category", "")),
                    tags=item.get("tags", []),
                    product_id="",
                    source_id=str(item.get("id", "")),
                )
            )
            stats.faq_documents += 1

    products_path = data_dir / "products.json"
    products = load_json(products_path)
    stats.source_files.append(str(products_path))
    for product in products:
        chunks = chunk_text(product_to_text(product))
        for index, chunk in enumerate(chunks):
            ids.append(f"product:{product['product_id']}:chunk:{index}")
            documents.append(chunk)
            metadatas.append(
                metadata(
                    source_type="product",
                    language="multi",
                    category=str(product.get("category", "")),
                    tags=product.get("tags", []),
                    product_id=str(product.get("product_id", "")),
                    source_id=str(product.get("product_id", "")),
                )
            )
            stats.product_documents += 1

    policies_path = data_dir / "policies.json"
    policies = load_json(policies_path)
    stats.source_files.append(str(policies_path))
    for policy in policies:
        for language, text in policy.get("content", {}).items():
            for index, chunk in enumerate(chunk_text(str(text))):
                ids.append(f"policy:{policy['id']}:{language}:chunk:{index}")
                documents.append(chunk)
                metadatas.append(
                    metadata(
                        source_type="policy",
                        language=language,
                        category=str(policy.get("category", "")),
                        tags=policy.get("tags", []),
                        product_id="",
                        source_id=str(policy.get("id", "")),
                    )
                )
                stats.policy_documents += 1

    stats.total_documents = len(ids)
    return ids, documents, metadatas, stats


def build_knowledge_base(
    data_dir: str | Path = DATA_DIR,
    collection_name: str = DEFAULT_COLLECTION,
    host: str | None = None,
    port: int | None = None,
    persist_dir: str | Path | None = None,
    reset: bool = False,
    batch_size: int = 64,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    strict_embedding: bool = False,
    force_fallback_embedding: bool = False,
) -> BuildStats:
    data_path = Path(data_dir)
    ids, documents, metadatas, stats = prepare_documents(data_path)

    embedder = EmbeddingModel(
        model_name=model_name,
        allow_fallback=not strict_embedding,
        force_fallback=force_fallback_embedding,
    )
    chroma = ChromaClientManager(host=host, port=port, persist_dir=persist_dir)

    if reset:
        chroma.reset_collection(collection_name)
    else:
        chroma.get_collection(collection_name)

    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        batch_docs = documents[start:end]
        batch_embeddings = embedder.encode(batch_docs)
        chroma.upsert(
            ids=ids[start:end],
            documents=batch_docs,
            embeddings=batch_embeddings,
            metadatas=metadatas[start:end],
            collection_name=collection_name,
        )
        print(f"upserted {min(end, len(ids))}/{len(ids)} documents")

    stats.collection_count = chroma.count(collection_name)
    stats.used_fallback_embedding = embedder.using_fallback

    print("Knowledge base build complete")
    print(f"FAQ documents: {stats.faq_documents}")
    print(f"Product documents: {stats.product_documents}")
    print(f"Policy documents: {stats.policy_documents}")
    print(f"Total prepared: {stats.total_documents}")
    print(f"Collection count: {stats.collection_count}")
    print(f"Embedding fallback: {stats.used_fallback_embedding}")
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the OmniSell RAG knowledge base into ChromaDB.")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Directory containing RAG JSON data files.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="ChromaDB collection name.")
    parser.add_argument("--host", default=None, help="ChromaDB HTTP host. Uses persistent local mode when omitted.")
    parser.add_argument("--port", type=int, default=None, help="ChromaDB HTTP port.")
    parser.add_argument("--persist-dir", default=None, help="Local ChromaDB persistent directory.")
    parser.add_argument("--reset", action="store_true", help="Delete and recreate the collection before building.")
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding/upsert batch size.")
    parser.add_argument("--model-name", default=DEFAULT_EMBEDDING_MODEL, help="SentenceTransformers model name.")
    parser.add_argument(
        "--strict-embedding", action="store_true", help="Fail instead of using the deterministic fallback embedder."
    )
    parser.add_argument(
        "--force-fallback-embedding",
        action="store_true",
        help="Use the deterministic fallback embedder for local smoke tests.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Only load and count documents; do not connect to ChromaDB."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dry_run:
        _, _, _, stats = prepare_documents(Path(args.data_dir))
        print(f"FAQ documents: {stats.faq_documents}")
        print(f"Product documents: {stats.product_documents}")
        print(f"Policy documents: {stats.policy_documents}")
        print(f"Total prepared: {stats.total_documents}")
        return

    build_knowledge_base(
        data_dir=args.data_dir,
        collection_name=args.collection,
        host=args.host,
        port=args.port,
        persist_dir=args.persist_dir,
        reset=args.reset,
        batch_size=args.batch_size,
        model_name=args.model_name,
        strict_embedding=args.strict_embedding,
        force_fallback_embedding=args.force_fallback_embedding,
    )


if __name__ == "__main__":
    main()
