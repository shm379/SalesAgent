"""Chroma vector store over the product catalog for semantic (RAG) search.

Embeddings try the configured LLM proxy first (EMBEDDING_PROVIDER=proxy) and
fall back to a local multilingual sentence-transformers model if the proxy
call fails or doesn't support embeddings, or if EMBEDDING_PROVIDER=local is
set explicitly. This keeps the project usable even if the Iranian proxy in
use doesn't expose an embeddings endpoint.
"""

from __future__ import annotations

import logging

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.config import get_settings
from app.data.products import load_products

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "products"


def get_embeddings() -> Embeddings:
    settings = get_settings()

    if settings.embedding_provider == "local":
        return _local_embeddings()

    try:
        from langchain_openai import OpenAIEmbeddings

        embeddings = OpenAIEmbeddings(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.embedding_model,
        )
        # Force a real call now so a broken proxy embeddings endpoint fails
        # here, at startup, instead of silently mid-conversation later.
        embeddings.embed_query("سلام")
        logger.info("Using proxy embeddings model=%s", settings.embedding_model)
        return embeddings
    except Exception as exc:  # noqa: BLE001 - deliberate broad fallback
        logger.warning(
            "Proxy embeddings unavailable (%s); falling back to local model %s",
            exc,
            settings.local_embedding_model,
        )
        return _local_embeddings()


class _E5PrefixedEmbeddings(Embeddings):
    """intfloat/multilingual-e5-* models need a 'query: '/'passage: ' prefix to
    perform well - without it, retrieval quality on short Persian queries
    (e.g. a bare category name) degrades badly. Verified empirically during
    development: without prefixes, a query like "جاروبرقی" (vacuum cleaner)
    returned camping gear and tools; with prefixes, it correctly returns
    vacuum cleaners as the top results."""

    def __init__(self, inner: Embeddings) -> None:
        self._inner = inner

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents([f"passage: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(f"query: {text}")


def _local_embeddings() -> Embeddings:
    from langchain_huggingface import HuggingFaceEmbeddings

    settings = get_settings()
    base = HuggingFaceEmbeddings(model_name=settings.local_embedding_model)
    if "e5" in settings.local_embedding_model.lower():
        return _E5PrefixedEmbeddings(base)
    return base


def _product_to_document(row) -> Document:
    text = "\n".join(
        [
            f"عنوان: {row['title']}",
            f"دسته: {row['category']}",
            f"برند: {row['brand']}",
            f"توضیحات: {row['description']}",
            f"ویژگی‌ها: {' | '.join(row['features_list'])}",
            f"برچسب‌ها: {' ، '.join(row['tags_list'])}",
        ]
    )
    metadata = {
        "id": int(row["id"]),
        "title": row["title"],
        "category": row["category"],
        "brand": row["brand"],
        "price": float(row["price"]),
        "stock": int(row["stock"]),
        "rating": float(row["rating"]),
        "features": row["features"],
    }
    return Document(page_content=text, metadata=metadata)


def build_vectorstore() -> Chroma:
    """Build (or rebuild) the Chroma index from the product catalog. Idempotent."""
    settings = get_settings()
    df = load_products()
    documents = [_product_to_document(row) for _, row in df.iterrows()]

    store = Chroma(
        collection_name=_COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=str(settings.chroma_dir),
    )
    existing_ids = store.get()["ids"]
    if existing_ids:
        store.delete(ids=existing_ids)

    ids = [str(doc.metadata["id"]) for doc in documents]
    store.add_documents(documents, ids=ids)
    return store


def get_vectorstore() -> Chroma:
    """Load the already-persisted Chroma index for query-time use."""
    settings = get_settings()
    if not settings.chroma_dir.exists():
        raise RuntimeError(
            f"Chroma index not found at {settings.chroma_dir}. "
            "Run `python scripts/build_index.py` first."
        )
    return Chroma(
        collection_name=_COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=str(settings.chroma_dir),
    )
