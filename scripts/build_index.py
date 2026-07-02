"""CLI: build (or rebuild) the Chroma index from the product CSV.

Usage:
    python scripts/build_index.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)


def main() -> None:
    from app.data.products import load_products
    from app.data.vectorstore import build_vectorstore

    df = load_products()
    print(f"Loaded {len(df)} products from CSV.")

    print("Building Chroma index (this may take a while on first run)...")
    store = build_vectorstore()
    print(f"Indexed {store._collection.count()} products into Chroma.")

    print("\nSample queries:")
    for query in ["یه لپ‌تاپ خوب برای دانشجو", "اسپیکر با باتری قوی", "جاروبرقی بدون کیسه"]:
        results = store.similarity_search(query, k=3)
        print(f"\n  query: {query}")
        for doc in results:
            print(f"    - {doc.metadata['title']} ({doc.metadata['category']}, {doc.metadata['price']:.0f} تومان)")


if __name__ == "__main__":
    main()
