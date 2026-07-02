"""One function per node in Graph_Final.xlsx.

Every node: reads state, does its work (LLM call / tool call / pure logic),
returns a PARTIAL state dict (never mutates state in place) per LangGraph
convention. Nodes that call the LLM proxy raise RecommendationToolError on
failure so the RetryPolicy attached to them in graph/builder.py can retry
transient network failures - this is the "Error Handling" + "Retry" pair the
project explicitly requires.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.data.products import category_matches, get_known_categories, normalize_persian
from app.graph.errors import RecommendationToolError
from app.graph.llm import get_llm
from app.graph.schemas import MessageAnalysis
from app.persona import (
    SYSTEM_PERSONA,
    build_clarifying_question_prompt,
    build_comparison_prompt,
    build_memory_answer_prompt,
    build_recommendation_prompt,
    build_similar_products_prompt,
    persona_for,
)
from app.state import SalesState
from app.tools.link_tool import scrape_product_url_tool
from app.tools.pandas_tool import pandas_filter_tool
from app.tools.rag_tool import rag_search_tool
from app.tools.vision_tool import VisionUnavailableError, describe_image_tool

_URL_PATTERN = re.compile(r"https?://\S+")


def _latest_human_text(state: SalesState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


_COLLOQUIAL_CATEGORY_SYNONYMS = {
    # colloquial/informal spelling or wording -> catalog's formal spelling.
    # Not exhaustive - just the confirmed gaps normalize_persian's ZWNJ/space
    # stripping alone can't bridge (different words/letters, not just spacing).
    "لباس": "پوشاک",
    "زنونه": "زنانه",
    "مردونه": "مردانه",
    "بچگونه": "بچگانه",
}


def _detect_explicit_category(raw_text: str) -> str | None:
    """Deterministic fallback: if the raw message names one of the catalog's
    known categories (allowing for common colloquial spelling), trust that
    over the LLM's own classification. The LLM's intent/category extraction
    is unreliable for short topic-switching questions (e.g. 'کتاب داری؟' or
    'لباس زنونه داری؟' right after an unrelated conversation sometimes gets
    misread as small talk about the OLD topic) - this catches that
    deterministically regardless of how analyze_message's LLM call behaves."""
    norm_text = normalize_persian(raw_text)
    for colloquial, formal in _COLLOQUIAL_CATEGORY_SYNONYMS.items():
        norm_text = norm_text.replace(normalize_persian(colloquial), normalize_persian(formal))
    for known_category in get_known_categories():
        if normalize_persian(known_category) in norm_text:
            return known_category
    return None


def preprocess_input(state: SalesState) -> dict:
    """Entry node. Normalizes photo/URL input into a synthetic Persian
    HumanMessage so every downstream node (analyze_message onward) only ever
    has to deal with text - photo and URL support both feed into the same
    recommendation pipeline as a text request, per the finalized diagram."""
    input_type = state.get("input_type", "text")

    if input_type == "photo":
        image_base64 = state.get("image_base64", "")
        try:
            description = describe_image_tool.invoke({"image_base64": image_base64})
        except VisionUnavailableError:
            fallback = (
                "کاربر یک عکس فرستاد ولی امکان تحلیل تصویر نبود. از کاربر به فارسی و "
                "مؤدبانه بخواه محصول موردنظرش رو با متن توضیح بده."
            )
            return {"messages": [HumanMessage(content=fallback)]}

        text = f"[کاربر یک تصویر از یک محصول فرستاد]\nتوضیح تصویر: {description}"
        return {"messages": [HumanMessage(content=text)], "image_description": description}

    # Auto-detect a URL in any text message (the caller doesn't need to
    # pre-classify "text" vs "url" - a plain Telegram text message that
    # happens to contain a link is handled the same way either way).
    raw_text = _latest_human_text(state)
    match = _URL_PATTERN.search(raw_text)
    if match:
        raw = scrape_product_url_tool.invoke({"url": match.group(0)})
        info = json.loads(raw)
        if info.get("error"):
            text = (
                f"[کاربر لینک محصولی فرستاد اما استخراج اطلاعات ناموفق بود: {info['error']}]\n"
                "از کاربر بخواه محصول موردنظرش رو با متن توضیح بده."
            )
            return {"messages": [HumanMessage(content=text)]}

        text = (
            "[کاربر لینک یک محصول از یک فروشگاه دیگر فرستاد]\n"
            f"عنوان صفحه: {info.get('title', '')}\n"
            f"توضیحات: {info.get('description', '')}\n"
            "یک محصول مشابه از فروشگاه ما پیدا کن."
        )
        return {"messages": [HumanMessage(content=text)], "scraped_product_info": info}

    return {}


def analyze_message(state: SalesState) -> dict:
    """Diagram box: 'تحلیل پیام و به‌روزرسانی State'. Also folds in the
    'قبلاً در Memory معرفی شده؟' and 'اطلاعات مشتری کامله؟' decisions by having
    the LLM classify intent + missing slots in one structured call."""
    raw_text = _latest_human_text(state)

    context = (
        f"{SYSTEM_PERSONA}\n\n"
        f"دسته‌بندی‌های موجود در فروشگاه:\n{', '.join(get_known_categories())}\n\n"
        "برای category، نزدیک‌ترین مورد از همین لیست رو انتخاب کن (فرقی نداره مشتری "
        "دقیقاً همون املا رو ننوشته باشه، مثلاً 'قهوه ساز' یعنی 'قهوه‌ساز')؛ فقط "
        "وقتی واقعاً هیچ‌کدوم مرتبط نبود، این فیلد رو null بذار (هرگز رشتهٔ خالی نده).\n\n"
        "دربارهٔ قیمت خیلی دقت کن، این دو تا کاملاً برعکس همدیگه‌ان:\n"
        "- 'زیر X'، 'حداکثر X'، 'بودجه‌ام X تومنه'، 'X تومن دارم' -> این یعنی سقف "
        "قیمته -> فیلد budget رو X بذار (min_budget رو دست نزن).\n"
        "- 'بالای X'، 'بیشتر از X'، 'حداقل X' -> این یعنی کف قیمته، مشتری دنبال "
        "چیزی گرون‌تر از X می‌گرده -> فیلد min_budget رو X بذار (budget رو دست نزن).\n"
        "مثال: 'بالای ۵ میلیون' یعنی min_budget=5000000 و budget باید null بمونه.\n\n"
        f"اطلاعات قبلی که از این مشتری داریم: "
        f"{json.dumps(state.get('user_profile', {}), ensure_ascii=False)}\n"
        f"محصولاتی که قبلاً به این مشتری معرفی شده‌اند: "
        f"{json.dumps(state.get('discussed_products', {}), ensure_ascii=False)}\n\n"
        + (
            "توجه: همین الان از مشتری یه سوال تکمیلی پرسیدیم؛ این پیام جدید تقریباً "
            "همیشه پاسخ به همون سواله (حتی اگه کوتاه یا مبهم باشه، مثل 'فرقی نداره' یا "
            "'مهم نیست')، نه یه پیام general/گپ‌وگفت جدا - این یعنی intent باید "
            "new_request بمونه.\n\n"
            if state.get("pending_clarification")
            else ""
        )
        + f"پیام جدید مشتری: {raw_text}\n\n"
        "این پیام را تحلیل کن."
    )

    try:
        analysis = get_llm(temperature=0).with_structured_output(MessageAnalysis).invoke(context)
    except Exception as exc:  # noqa: BLE001
        raise RecommendationToolError(f"Message analysis failed: {exc}") from exc

    intent = analysis.intent
    discussed = state.get("discussed_products") or {}
    previous_category = (state.get("user_profile") or {}).get("category")

    # A reply to a clarifying question we JUST asked (e.g. "فرقی نداره" / "no
    # preference" answering "what features matter to you?") should never be
    # read as content-free small talk - that discards the whole in-progress
    # request and makes the bot look like it "forgot" what was being discussed.
    if state.get("pending_clarification") and intent == "general":
        intent = "new_request"

    detected_category = _detect_explicit_category(raw_text)
    effective_category = detected_category or analysis.category
    if detected_category and previous_category and not category_matches(detected_category, previous_category):
        # Trust the keyword hit over whatever intent the LLM produced - a
        # message that literally names a different catalog category is never
        # small talk or a memory-only follow-up about the OLD topic.
        intent = "new_request"
    # Deterministic safety net, not just prompt wording: "known_product_question"
    # and "comparison" are only meaningful if there's actually something in
    # memory to answer from / compare. A terse noun-phrase message (e.g. "جارو
    # برقی دایسون") can otherwise get misread as "ask about a known thing" on
    # the very first turn of a conversation, skipping the search tools
    # entirely and answering from an empty discussed_products.
    if intent == "known_product_question" and not discussed:
        intent = "new_request"
    if intent == "comparison" and len(discussed) < 2:
        intent = "new_request"

    # Narrower case of the same problem: memory is non-empty, but the brand the
    # customer is now asking about was never actually part of it (e.g. we
    # discussed Samsung/Eurica vacuums, then customer asks "دایسون داری؟" -
    # that's a fresh availability question, not a memory lookup, even though
    # discussed_products isn't empty). Answering "no" from memory alone here
    # would be wrong without ever having searched for Dyson at all.
    if intent == "known_product_question" and analysis.brand:
        brand_in_memory = any(
            category_matches(analysis.brand, item.get("brand") or "") for item in discussed.values()
        )
        if not brand_in_memory:
            intent = "new_request"

    # Some proxies emit "" instead of a true JSON null for an unset optional
    # string field despite the schema - treat blank strings the same as None,
    # otherwise an empty category/brand silently wipes out a real one already
    # in the (merged) profile from an earlier turn.
    profile_update = {
        key: value
        for key, value in {
            "budget": analysis.budget,
            "min_budget": analysis.min_budget,
            "category": effective_category,
            "brand": analysis.brand,
            "excluded_brand": analysis.excluded_brand,
            "preferences": analysis.preferences,
        }.items()
        if value not in (None, "")
    }
    # Defensive: never let the same brand be simultaneously "wanted" and
    # "excluded". This must check against BOTH this turn's own extraction
    # (LLM double-extracted it despite the schema guidance) AND whatever
    # brand is already sitting in the merged profile from an earlier turn -
    # otherwise excluding a brand doesn't clear a stale "brand" from before,
    # and both keys end up with the same value simultaneously.
    new_excluded = profile_update.get("excluded_brand")
    if new_excluded:
        new_brand = profile_update.get("brand")
        old_brand = (state.get("user_profile") or {}).get("brand")
        if new_brand and category_matches(new_brand, new_excluded):
            profile_update.pop("brand", None)
        elif "brand" not in profile_update and old_brand and category_matches(old_brand, new_excluded):
            profile_update["brand"] = None

    # A budget/brand extracted for one product category almost never carries
    # over to a completely different one (e.g. an Apple-laptop budget/brand
    # has no bearing on a coffee-maker request). Without this, user_profile's
    # merge reducer keeps stale slots forever once set, and the bot starts
    # asking nonsensical questions like "which Apple coffee maker model?".
    if effective_category and previous_category and not category_matches(effective_category, previous_category):
        for stale_key in ("budget", "min_budget", "brand", "excluded_brand", "preferences"):
            profile_update.setdefault(stale_key, None)

    result = {
        "raw_input_text": raw_text,
        "intent": intent,
        "missing_slots": analysis.missing_slots,
        "user_profile": profile_update,
        "last_user_message_at": datetime.now(timezone.utc).isoformat(),
        "used_similar_fallback": False,
        "pending_clarification": False,
    }
    if analysis.purchase_confirmed:
        result["purchase_confirmed"] = True
    return result


def ask_clarifying_question(state: SalesState) -> dict:
    """Diagram box: 'دریافت سوالات تکمیلی از مشتری'."""
    return {"pending_clarification": True}


def _manual_tool_call_fallback(state: SalesState) -> AIMessage:
    """Used when the LLM proxy doesn't populate .tool_calls (see
    scripts/test_tool_calling.py and the README's known-limitations section).
    Builds an equivalent AIMessage with tool_calls constructed from the
    already-extracted user_profile, so downstream ToolNode execution is
    identical either way."""
    profile = state.get("user_profile", {})
    query = profile.get("preferences") or profile.get("category") or state.get("raw_input_text", "")

    tool_calls = [
        {
            "name": "pandas_filter_tool",
            "args": {
                "category": profile.get("category"),
                "brand": profile.get("brand"),
                "exclude_brand": profile.get("excluded_brand"),
                "max_price": profile.get("budget"),
                "min_price": profile.get("min_budget"),
            },
            "id": f"call_{uuid.uuid4().hex[:12]}",
        },
        {
            "name": "rag_search_tool",
            "args": {"query": query},
            "id": f"call_{uuid.uuid4().hex[:12]}",
        },
    ]
    return AIMessage(content="", tool_calls=tool_calls)


def call_recommendation_tools(state: SalesState) -> dict:
    """Diagram box: 'توصیه محصول (RAG+Pandas) با Tool Calling'. The LLM decides
    tool arguments (and, ideally, which tools to call); ToolNode executes both
    pandas_filter_tool and rag_search_tool. Falls back to manual tool-call
    construction if the proxy doesn't support bind_tools reliably."""
    profile = state.get("user_profile", {})
    prompt = (
        f"{SYSTEM_PERSONA}\n\n"
        f"نیاز مشتری تا الان: {json.dumps(profile, ensure_ascii=False)}\n\n"
        "برای پیدا کردن بهترین محصولات، هر دو ابزار pandas_filter_tool و "
        "rag_search_tool را با آرگومان‌های مناسب فراخوانی کن. اگر excluded_brand "
        "توی نیاز مشتری هست، حتماً همون رو به‌عنوان exclude_brand به "
        "pandas_filter_tool بده تا اون برند از نتایج حذف بشه. توجه: budget یعنی "
        "سقف قیمت (max_price) و min_budget یعنی کف قیمت (min_price) - این دو "
        "کاملاً برعکس همدیگه‌ان، جابه‌جاشون نکن."
    )

    try:
        llm_with_tools = get_llm().bind_tools([pandas_filter_tool, rag_search_tool])
        response = llm_with_tools.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        raise RecommendationToolError(f"LLM proxy tool-calling request failed: {exc}") from exc

    if not response.tool_calls:
        response = _manual_tool_call_fallback(state)

    return {"messages": [response]}


def merge_tool_results(state: SalesState) -> dict:
    """Combine + dedupe the pandas_filter_tool and rag_search_tool outputs that
    ToolNode appended to state['messages'], into a single ranked product list."""
    messages = state.get("messages", [])

    last_ai_index = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            last_ai_index = index
            break

    if last_ai_index is None:
        return {"matched_products": []}

    tool_messages = [m for m in messages[last_ai_index + 1 :] if isinstance(m, ToolMessage)]

    combined: dict[str, dict] = {}
    for tool_message in tool_messages:
        try:
            items = json.loads(tool_message.content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RecommendationToolError(f"Malformed tool output: {exc}") from exc
        for item in items:
            product_id = str(item.get("id"))
            if product_id and product_id not in combined:
                combined[product_id] = item

    # RAG always returns its k nearest neighbours even when nothing is truly
    # relevant (vector search has no concept of "no match") - e.g. a request
    # for a nonexistent category still comes back with 8 semi-random products.
    # If the customer named a category, keep only items that actually match
    # it; an empty result here is correct and should fall through to
    # suggest_similar_products, not be papered over with unrelated products.
    profile = state.get("user_profile", {}) or {}
    category = profile.get("category")
    excluded_brand = profile.get("excluded_brand")
    max_price = profile.get("budget")
    min_price = profile.get("min_budget")
    candidates = list(combined.values())
    if category:
        candidates = [p for p in candidates if category_matches(category, p.get("category") or "")]
    if excluded_brand:
        # rag_search_tool has no concept of exclusion, so a semantically close
        # match from the excluded brand can still slip in here even though
        # pandas_filter_tool was told to leave it out - strip it explicitly.
        candidates = [p for p in candidates if not category_matches(excluded_brand, p.get("brand") or "")]
    if max_price is not None:
        # Same issue for price: rag_search_tool has no price awareness at all,
        # so a semantically close but over-budget item can slip in even though
        # pandas_filter_tool correctly respected max_price.
        candidates = [p for p in candidates if (p.get("price") or 0) <= max_price]
    if min_price is not None:
        candidates = [p for p in candidates if (p.get("price") or 0) >= min_price]

    matched = sorted(candidates, key=lambda p: p.get("rating", 0) or 0, reverse=True)
    return {"matched_products": matched[:5]}


def _to_discussed_entry(product: dict) -> dict:
    return {
        "title": product.get("title"),
        "price": product.get("price"),
        "category": product.get("category"),
        "brand": product.get("brand"),
        "rating": product.get("rating"),
        "features": product.get("features", ""),
    }


def introduce_products(state: SalesState) -> dict:
    """Diagram box: 'معرفی حداقل ۳ محصول' (+ shares this outcome with a
    comparison request, per the finalized diagram's note)."""
    products = state.get("matched_products", [])[:5]
    discussed = {str(p["id"]): _to_discussed_entry(p) for p in products if p.get("id") is not None}
    return {
        "discussed_products": discussed,
        "last_shown_product_ids": list(discussed.keys()),
    }


def suggest_similar_products(state: SalesState) -> dict:
    """Diagram box: 'پیشنهاد محصولات مشابه در صورت موجود نبودن محصول موردنظر'.
    Runs a relaxed RAG-only search directly (bypassing price/brand constraints)
    since the strict combined search came back empty."""
    profile = state.get("user_profile", {})
    category = profile.get("category")
    # category anchors the query - preferences/raw_text alone can be
    # non-informative filler (e.g. a customer answering "فرقی نداره" to a
    # brand question), which previously sent the RAG query semantically
    # adrift into a totally unrelated category (e.g. toys for a fryer
    # request). Combine everything so the category always grounds the search.
    query_parts = [part for part in (category, profile.get("brand"), profile.get("preferences")) if part]
    query = " ".join(query_parts) or state.get("raw_input_text", "")

    try:
        raw = rag_search_tool.invoke({"query": query, "k": 8})
        items = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise RecommendationToolError(f"Similar-product fallback search failed: {exc}") from exc

    if category:
        # Same defense-in-depth as merge_tool_results: even a well-formed
        # query can't guarantee RAG stays on-category, so filter explicitly
        # rather than trusting semantic similarity alone.
        on_category = [p for p in items if category_matches(category, p.get("category") or "")]
        if on_category:
            items = on_category

    top = items[:3]
    discussed = {str(p["id"]): _to_discussed_entry(p) for p in top if p.get("id") is not None}
    return {
        "matched_products": top,
        "discussed_products": discussed,
        "used_similar_fallback": True,
        "last_shown_product_ids": list(discussed.keys()),
    }


def generate_final_response(state: SalesState) -> dict:
    """Diagram box: 'تولید پاسخ نهایی با لحن فروشنده'. The single place that
    composes the actual reply text, choosing the right persona prompt for
    whichever branch fired."""
    intent = state.get("intent", "general")

    if state.get("pending_clarification"):
        prompt = build_clarifying_question_prompt(state)
    elif intent == "known_product_question":
        prompt = build_memory_answer_prompt(state)
    elif intent == "comparison":
        prompt = build_comparison_prompt(state)
    elif intent == "new_request" and state.get("used_similar_fallback"):
        prompt = build_similar_products_prompt(state)
    elif intent == "new_request":
        prompt = build_recommendation_prompt(state)
    else:
        prompt = f"{persona_for(state)}\n\nبه پیام مشتری به‌صورت طبیعی و کوتاه پاسخ بده."

    try:
        response = get_llm().invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        raise RecommendationToolError(f"Final response generation failed: {exc}") from exc

    final_text = str(response.content)
    # Deliberately NOT touching pending_clarification here: if
    # ask_clarifying_question ran earlier THIS SAME turn, it already set it to
    # True and that must survive into the next turn (so analyze_message can
    # recognize the customer's next message as an answer to it) - clobbering
    # it back to False here would erase that signal every single time.
    return {
        "final_response": final_text,
        "messages": [AIMessage(content=final_text)],
    }
