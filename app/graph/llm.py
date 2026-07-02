"""Shared ChatOpenAI client construction, pointed at whatever OpenAI-compatible
proxy is configured in .env. Centralized so every node builds the client the
same way."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.4) -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=temperature,
    )
