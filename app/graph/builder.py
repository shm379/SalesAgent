"""Assembles the compiled StateGraph from nodes.py + routing.py, matching
Graph_Final.xlsx. Takes the checkpointer as a parameter (not constructed
inside) so scripts/chat_cli.py can pass MemorySaver/AsyncSqliteSaver and
bot/telegram_bot.py can pass the shared AsyncSqliteSaver."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import RetryPolicy

from app.graph.errors import RecommendationToolError
from app.graph.nodes import (
    analyze_message,
    ask_clarifying_question,
    call_recommendation_tools,
    generate_final_response,
    introduce_products,
    merge_tool_results,
    preprocess_input,
    suggest_similar_products,
)
from app.graph.routing import route_after_analysis, route_after_match_check
from app.state import SalesState
from app.tools.pandas_tool import pandas_filter_tool
from app.tools.rag_tool import rag_search_tool

# Retry: transient LLM-proxy / tool-execution failures get retried up to 3
# times with backoff before the graph gives up on that node. Error Handling:
# every LLM/tool-calling node in nodes.py wraps its risky call and raises this
# specific exception (instead of a generic one) so retry_on can target it.
_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0, retry_on=(RecommendationToolError,))


def build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(SalesState)

    builder.add_node("preprocess_input", preprocess_input, retry_policy=_RETRY)
    builder.add_node("analyze_message", analyze_message, retry_policy=_RETRY)
    builder.add_node("ask_clarifying_question", ask_clarifying_question)
    builder.add_node("call_recommendation_tools", call_recommendation_tools, retry_policy=_RETRY)
    builder.add_node(
        "execute_tools",
        ToolNode([pandas_filter_tool, rag_search_tool], handle_tool_errors=False),
        retry_policy=_RETRY,
    )
    builder.add_node("merge_tool_results", merge_tool_results, retry_policy=_RETRY)
    builder.add_node("introduce_products", introduce_products)
    builder.add_node("suggest_similar_products", suggest_similar_products, retry_policy=_RETRY)
    builder.add_node("generate_final_response", generate_final_response, retry_policy=_RETRY)

    builder.set_entry_point("preprocess_input")
    builder.add_edge("preprocess_input", "analyze_message")

    # State Management: SalesState flows through every node above.
    # Conditional Routing: the three decisions below.
    builder.add_conditional_edges(
        "analyze_message",
        route_after_analysis,
        {
            "ask_clarifying_question": "ask_clarifying_question",
            "call_recommendation_tools": "call_recommendation_tools",
            "generate_final_response": "generate_final_response",
        },
    )

    # Tool Calling: bind_tools + ToolNode + tools_condition, wired FORWARD only
    # (never back to call_recommendation_tools) - a bounded step, not a ReAct loop.
    builder.add_conditional_edges(
        "call_recommendation_tools",
        tools_condition,
        {"tools": "execute_tools", END: "merge_tool_results"},
    )
    builder.add_edge("execute_tools", "merge_tool_results")

    builder.add_conditional_edges(
        "merge_tool_results",
        route_after_match_check,
        {
            "introduce_products": "introduce_products",
            "suggest_similar_products": "suggest_similar_products",
        },
    )

    builder.add_edge("ask_clarifying_question", "generate_final_response")
    builder.add_edge("introduce_products", "generate_final_response")
    builder.add_edge("suggest_similar_products", "generate_final_response")
    builder.add_edge("generate_final_response", END)

    return builder.compile(checkpointer=checkpointer)
