"""Custom exceptions used to make LangGraph's RetryPolicy target specific,
expected failure modes instead of relying on its default retry_on (which does
not catch generic exceptions)."""


class RecommendationToolError(Exception):
    """Raised when pandas_filter_tool/rag_search_tool execution fails (network,
    empty/malformed tool-call args, Chroma unavailable, etc). RetryPolicy on the
    call_recommendation_tools node targets this exception explicitly."""
