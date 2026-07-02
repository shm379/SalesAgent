"""LangChain tool wrapper for describing a product photo via a vision-capable
chat model - supports 'send a photo, find something similar in the store'."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from app.graph.llm import get_llm


class VisionUnavailableError(Exception):
    """Raised when the configured model/proxy can't process the image (model
    doesn't support vision, malformed image data, network failure, ...). The
    calling node degrades gracefully to asking the customer to describe the
    item in text instead of crashing the turn."""


@tool
def describe_image_tool(image_base64: str) -> str:
    """Describe a product photo in Persian: what kind of product it is, its
    likely category, and visible brand/color/style - enough detail to drive a
    semantic catalog search. Use this when the customer sends a photo instead
    of text.

    Args:
        image_base64: base64-encoded JPEG/PNG image bytes.
    """
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    "این عکس یک محصول است. به فارسی توضیح بده این محصول چیست، "
                    "احتمالاً چه دسته‌بندی‌ای داره (مثلاً اسپیکر، کفش، لپ‌تاپ و ...)، "
                    "و چه رنگ/برند/سبکی داره. فقط توضیح کوتاه و دقیق بده، چیز دیگه‌ای ننویس."
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        ]
    )
    try:
        response = get_llm(temperature=0).invoke([message])
    except Exception as exc:  # noqa: BLE001
        raise VisionUnavailableError(f"Vision request failed: {exc}") from exc
    return str(response.content)
