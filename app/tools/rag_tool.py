"""LangChain tool wrapper around the Chroma vector store.

This is the "RAG" half of the combined RAG+Pandas recommendation step.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from app.data.vectorstore import get_vectorstore


@tool
def rag_search_tool(query: str, k: int = 8) -> str:
    """Semantic search over product titles/descriptions/features/tags. Use this
    for fuzzy, descriptive, or vague customer requests (e.g. "something good for
    gaming", "quiet vacuum for a small apartment") where exact category/brand/price
    filters alone would miss relevant products.

    Args:
        query: a natural-language description of what the customer wants, in Persian.
        k: how many results to return.
    """
    store = get_vectorstore()
    results = store.similarity_search(query, k=k)
    payload = [
        {
            "id": doc.metadata["id"],
            "title": doc.metadata["title"],
            "category": doc.metadata["category"],
            "brand": doc.metadata["brand"],
            "price": doc.metadata["price"],
            "rating": doc.metadata["rating"],
            "stock": doc.metadata["stock"],
            "features": doc.metadata.get("features", ""),
        }
        for doc in results
    ]
    return json.dumps(payload, ensure_ascii=False)
