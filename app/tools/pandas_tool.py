"""LangChain tool wrapper around app/data/products.py's structured filter.

This is the "Pandas" half of the combined RAG+Pandas recommendation step.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from app.data.products import filter_products


@tool
def pandas_filter_tool(
    category: str | None = None,
    brand: str | None = None,
    exclude_brand: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
) -> str:
    """Structured filter over the product catalog using exact fields (category,
    brand, price range, minimum rating). Use this when the customer gave concrete
    constraints like a budget, a specific brand, or a product category/type.
    Only returns in-stock products, sorted by rating then review count.

    Args:
        category: Persian product category, e.g. "اسپیکر", "لپ‌تاپ", "جاروبرقی".
        brand: Persian brand name the customer WANTS, e.g. "سامسونگ", "بوش".
        exclude_brand: Persian brand name the customer explicitly does NOT
            want (e.g. customer said "دایسون نه" or "غیر از دایسون").
        min_price: minimum price in Toman.
        max_price: maximum price in Toman (use this for a customer's budget ceiling).
        min_rating: minimum product rating (0-5).
    """
    result = filter_products(
        category=category,
        brand=brand,
        exclude_brand=exclude_brand,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        in_stock_only=True,
        limit=10,
    )
    columns = ["id", "title", "category", "brand", "price", "discount", "rating", "stock", "features"]
    return json.dumps(result[columns].to_dict(orient="records"), ensure_ascii=False)
