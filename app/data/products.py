"""Loads products_500.csv into a cached DataFrame and provides plain (non-LangChain)
structured filtering. Kept separate from app/tools/pandas_tool.py so this is
independently unit-testable without any LangChain machinery.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd

from app.config import get_settings

NUMERIC_COLUMNS = ["price", "discount", "stock", "rating", "review_count"]

_ZWNJ = "‌"


def normalize_persian(text: str) -> str:
    """Strip ZWNJ / regular spaces / half-spaces and lowercase, so LLM-extracted
    slots like 'لپ تاپ' or 'لپتاپ' reliably match the catalog's canonical
    'لپ‌تاپ' spelling regardless of exactly how the separator was typed."""
    return text.replace(_ZWNJ, "").replace(" ", "").replace("‌", "").strip().lower()


def category_matches(query: str, actual: str) -> bool:
    norm_query, norm_actual = normalize_persian(query), normalize_persian(actual)
    if not norm_query or not norm_actual:
        return False
    return norm_query in norm_actual or norm_actual in norm_query


@lru_cache(maxsize=1)
def load_products() -> pd.DataFrame:
    settings = get_settings()
    df = pd.read_csv(settings.products_csv_path)

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # "صدای ۳۶۰ درجه | اتصال چندگانه | بیس قوی" -> list[str]
    df["features_list"] = df["features"].apply(
        lambda s: [f.strip() for f in str(s).split("|") if f.strip()]
    )
    # "بلندگو، سفر، پارتی، اسپیکر" -> list[str]
    df["tags_list"] = df["tags"].apply(
        lambda s: [t.strip() for t in str(s).replace("،", ",").split(",") if t.strip()]
    )

    return df


@lru_cache(maxsize=1)
def get_known_categories() -> tuple[str, ...]:
    """The catalog's actual category strings, for grounding the LLM's slot
    extraction so it doesn't invent a category name that's spelled slightly
    differently from what's in the data (see normalize_persian docstring)."""
    return tuple(sorted(load_products()["category"].unique()))


def filter_products(
    df: pd.DataFrame | None = None,
    category: str | None = None,
    brand: str | None = None,
    exclude_brand: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    in_stock_only: bool = True,
    limit: int = 20,
) -> pd.DataFrame:
    """Structured filter over the product catalog.

    category/brand use case-insensitive substring matching (not exact match)
    since an LLM-extracted slot may not match the CSV's exact Persian spelling.
    """
    data = df if df is not None else load_products()
    result = data

    if category:
        norm_query = normalize_persian(category)
        mask = result["category"].apply(lambda actual: norm_query in normalize_persian(str(actual)))
        result = result[mask]
    if brand:
        norm_query = normalize_persian(brand)
        mask = result["brand"].apply(lambda actual: norm_query in normalize_persian(str(actual)))
        result = result[mask]
    if exclude_brand:
        norm_excl = normalize_persian(exclude_brand)
        mask = result["brand"].apply(lambda actual: norm_excl not in normalize_persian(str(actual)))
        result = result[mask]
    if min_price is not None:
        result = result[result["price"] >= min_price]
    if max_price is not None:
        result = result[result["price"] <= max_price]
    if min_rating is not None:
        result = result[result["rating"] >= min_rating]
    if in_stock_only:
        result = result[result["stock"] > 0]

    return result.sort_values(["rating", "review_count"], ascending=False).head(limit)
