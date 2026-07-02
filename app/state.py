"""LangGraph state schema for the sales assistant.

Maps to the finalized graph (Graph_Final.xlsx): messages carry the LangGraph
short-term conversation memory, while user_profile/discussed_products are the
long-term "don't ask twice, don't re-search what's already known" memory
required by the project brief. Both dict fields use a merge reducer so a node
returning a partial update (e.g. just {"budget": ...}) doesn't clobber the rest.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

InputType = Literal["text", "photo"]
"""URLs aren't declared separately - preprocess_input auto-detects a URL
inside any text message, so the caller only ever needs to set 'photo' when
sending an image; everything else is 'text' (the default)."""
Intent = Literal["new_request", "comparison", "known_product_question", "general"]


def merge_dicts(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    return {**(old or {}), **(new or {})}


class SalesState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]

    # Long-term memory (persisted via the checkpointer across the whole thread).
    user_profile: Annotated[dict[str, Any], merge_dicts]
    """Slots learned so far: budget, category, brand, preferences, etc."""

    discussed_products: Annotated[dict[str, Any], merge_dicts]
    """product_id (str) -> {title, price, reason, ...} for every product already
    introduced to this user, so follow-up questions can be answered from memory
    without re-running RAG/Pandas."""

    # Per-turn scratch fields (still persisted, but meaningful only for the
    # current turn's routing decisions).
    input_type: InputType
    intent: Intent
    raw_input_text: str
    image_base64: str
    """Set by the caller (Telegram handler / chat_cli) when input_type == 'photo'."""
    image_description: str
    scraped_product_info: dict[str, Any]

    missing_slots: list[str]
    matched_products: list[dict[str, Any]]
    used_similar_fallback: bool
    last_shown_product_ids: list[str]
    """ids of the products shown in the most recent recommendation batch, so a
    follow-up like 'compare these two' can be scoped correctly instead of
    comparing every product ever discussed in the whole conversation."""
    final_response: str

    last_user_message_at: str  # ISO 8601 timestamp
    purchase_confirmed: bool
    pending_clarification: bool
