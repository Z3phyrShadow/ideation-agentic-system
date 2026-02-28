"""
llm.py
------
Initializes and exposes the Gemini LLM instance via the LangChain adapter.

Two factory functions are provided:
    get_llm()             — bare LLM, no tools bound (used internally, e.g.
                            inside summarize_document to avoid circular binding).
    get_llm_with_tools()  — LLM with all document-ingestion tools bound;
                            used by the agent node in graph.py.
"""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from discord_agent.config import GEMINI_API_KEY


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
    Return a cached LLM instance with all document-ingestion tools bound.

    Lazy-imports ALL_TOOLS from tools.py to avoid a circular import at
    module load time (tools.py imports get_llm from this module).
    """
    from discord_agent.tools import ALL_TOOLS  # deferred import
    return get_llm().bind_tools(ALL_TOOLS)
