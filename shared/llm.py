"""
llm.py
------
Initializes and exposes the Gemini LLM instance via the LangChain adapter.

Two factory functions are provided:
    get_llm()             — bare LLM, no tools bound (used internally).
    get_llm_with_tools()  — LLM with all ideation tools bound;
                            used by the ideation agent in agents/ideation/graph.py.
"""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from discord_bot.config import GEMINI_API_KEY


@lru_cache(maxsize=1)
def get_llm() -> ChatGoogleGenerativeAI:
    """
    Return a cached bare ChatGoogleGenerativeAI instance (no tools bound).

    Used directly for internal LLM calls (e.g. summarize_document) where
    tool binding is not needed or would cause circular imports.
    """
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.7,
    )


@lru_cache(maxsize=1)
def get_llm_with_tools() -> BaseChatModel:
    """
    Return a cached LLM instance with all ideation document tools bound.

    Lazy-imports ALL_TOOLS from the tools package to avoid a circular import
    at module load time (tools imports get_llm from this module).
    """
    from tools import ALL_TOOLS  # deferred import
    return get_llm().bind_tools(ALL_TOOLS)
