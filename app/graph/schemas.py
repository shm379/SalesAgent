"""Pydantic schemas for structured LLM output used inside graph nodes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MessageAnalysis(BaseModel):
    """Structured extraction from the latest user message + conversation memory."""

    intent: Literal["new_request", "comparison", "known_product_question", "general"] = Field(
        description=(
            "new_request: customer wants a product recommendation (new or updated "
            "budget/brand/category). comparison: customer asks to compare products "
            "already discussed. known_product_question: customer asks about a "
            "specific product that is already in discussed_products - answerable "
            "from memory alone. general: greeting/small talk/store policy question."
        )
    )
    budget: int | None = Field(
        default=None,
        description=(
            "Customer's MAXIMUM budget / price ceiling in Toman, from phrasing "
            "like 'زیر ۵ میلیون', 'حداکثر ۵ میلیون', 'بودجه‌ام ۵ میلیونه'. "
            "Do NOT put a minimum-price phrase here - see min_budget."
        ),
    )
    min_budget: int | None = Field(
        default=None,
        description=(
            "Customer's MINIMUM price / floor in Toman, from phrasing like "
            "'بالای ۵ میلیون', 'حداقل ۵ میلیون', 'بیشتر از ۵ میلیون' (they want "
            "something that costs AT LEAST this much, e.g. for higher quality). "
            "This is the opposite of budget - never confuse the two."
        ),
    )
    category: str | None = Field(default=None, description="Persian product category, if identifiable.")
    brand: str | None = Field(
        default=None,
        description=(
            "Persian brand name the customer WANTS, if mentioned. Pay close "
            "attention to negation ('نه', 'غیر از', 'بجز', 'به‌جز'): if the "
            "customer names a brand they do NOT want (e.g. 'دایسون نه', 'غیر "
            "از دایسون'), that brand goes in excluded_brand instead, NEVER here."
        ),
    )
    excluded_brand: str | None = Field(
        default=None,
        description=(
            "A brand the customer explicitly rejected/excluded, e.g. 'دایسون "
            "نه' or 'غیر از دایسون' or 'بجز سامسونگ' -> excluded_brand='دایسون'"
            "/'سامسونگ'. Only set this from explicit negation, never guess."
        ),
    )
    preferences: str | None = Field(
        default=None, description="Any other stated preference/usage/feature, free text in Persian."
    )
    missing_slots: list[str] = Field(
        default_factory=list,
        description=(
            "Which of budget/category/brand/usage are still missing and needed "
            "before a good recommendation can be made. Empty list if enough is "
            "known already (be lenient - don't demand every slot)."
        ),
    )
    purchase_confirmed: bool = Field(
        default=False,
        description=(
            "True only if the customer just explicitly said they decided to buy "
            "/ ordered / confirmed purchase of a specific product (e.g. 'همینو "
            "می‌خرم', 'باشه سفارش بده'). False for anything else, including just "
            "expressing interest."
        ),
    )
