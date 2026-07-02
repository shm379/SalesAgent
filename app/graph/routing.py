"""Conditional edge functions - one per decision diamond in Graph_Final.xlsx.

Each function is pure: (state) -> next node name. No side effects, no LLM
calls here (all LLM/tool work happens in nodes.py) - this is what LangGraph's
own guidance recommends for conditional edges.
"""

from __future__ import annotations

from app.state import SalesState


def route_after_analysis(state: SalesState) -> str:
    """Diagram boxes: 'قبلاً در Memory معرفی شده؟' + 'مقایسه محصولات درخواست شده؟'
    + 'اطلاعات مشتری کامله؟', folded into one classification done in analyze_message.
    """
    intent = state.get("intent", "general")

    if intent == "known_product_question":
        # Memory-check "yes": answer directly, no new RAG/Pandas search.
        return "generate_final_response"

    if intent == "comparison":
        # Comparison reuses already-discussed products, no new search needed.
        return "generate_final_response"

    if intent == "new_request":
        if state.get("missing_slots"):
            return "ask_clarifying_question"
        return "call_recommendation_tools"

    return "generate_final_response"


def route_after_tools(state: SalesState) -> str:
    """After the ToolNode runs pandas_filter_tool/rag_search_tool: always merge
    results next (never loop back to the LLM tool-calling node)."""
    return "merge_tool_results"


def route_after_match_check(state: SalesState) -> str:
    """Diagram box: 'محصول مورد نظر مشتری رو داریم؟'"""
    if state.get("matched_products"):
        return "introduce_products"
    return "suggest_similar_products"
