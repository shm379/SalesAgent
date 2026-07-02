"""LangChain tool wrapper to scrape a product URL for title/description,
best-effort - supports 'send a link, find something similar in our store'.
Deliberately fails soft (returns a partial/error dict, never raises) since
external site scraping is inherently unreliable and this feeds into a RAG
search anyway, not an exact-match lookup."""

from __future__ import annotations

import json

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

_TIMEOUT_SECONDS = 8
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SalesAssistantBot/1.0)"}


@tool
def scrape_product_url_tool(url: str) -> str:
    """Fetch a product page URL and extract whatever title/description can be
    found (best-effort - many sites will only yield partial info, that's
    fine). Use this when the customer sends a link to a product on another
    website.

    Args:
        url: the product page URL the customer sent.
    """
    result: dict = {"url": url, "title": "", "description": ""}
    try:
        response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        result["error"] = f"صفحه قابل دریافت نبود: {exc}"
        return json.dumps(result, ensure_ascii=False)

    soup = BeautifulSoup(response.text, "html.parser")

    if soup.title and soup.title.string:
        result["title"] = soup.title.string.strip()

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        result["title"] = og_title["content"].strip()

    meta_description = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta_description and meta_description.get("content"):
        result["description"] = meta_description["content"].strip()

    if not result["title"] and not result["description"]:
        result["error"] = "متن قابل استفاده‌ای از این صفحه استخراج نشد."

    return json.dumps(result, ensure_ascii=False)
