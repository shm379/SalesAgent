"""Standalone smoke test: does the configured LLM proxy actually populate
.tool_calls when bind_tools is used? Some OpenAI-compatible proxies return
plain text instead (see langchain-ai/langchain#21887). Run this BEFORE
building/trusting the ToolNode-based recommendation node.

Usage:
    python scripts/test_tool_calling.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    from langchain_core.tools import tool

    from app.graph.llm import get_llm

    @tool
    def get_weather(city: str) -> str:
        """Get the current weather for a city."""
        return f"sunny in {city}"

    print("1) Plain chat call (no tools)...")
    llm = get_llm()
    response = llm.invoke("فقط بگو سلام، چیز دیگه‌ای ننویس.")
    print("   OK ->", response.content[:200])

    print("\n2) bind_tools + a request that should trigger a tool call...")
    llm_with_tools = llm.bind_tools([get_weather])
    response = llm_with_tools.invoke("هوای تهران الان چطوره؟ از ابزار موجود استفاده کن.")
    print("   tool_calls:", response.tool_calls)

    if response.tool_calls:
        print("\n✅ SUCCESS: proxy populates .tool_calls correctly.")
        print("   -> app/graph/nodes.py's call_recommendation_tools can trust bind_tools.")
    else:
        print("\n⚠️  WARNING: .tool_calls came back empty.")
        print("   -> This proxy/model does not reliably support LangChain tool calling.")
        print("   -> The manual fallback path in call_recommendation_tools will be used")
        print("      instead (already built in, see app/graph/nodes.py:_manual_tool_call_fallback).")
        sys.exit(1)


if __name__ == "__main__":
    main()
