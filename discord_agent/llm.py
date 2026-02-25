"""
llm.py
------
Initializes and exposes the Gemini LLM instance via the LangChain adapter.
The model is instantiated once and reused across all graph invocations.
"""

from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

from discord_agent.config import GEMINI_API_KEY


@lru_cache(maxsize=1)
def get_llm() -> ChatGoogleGenerativeAI:
    """
    Return a cached ChatGoogleGenerativeAI instance for Gemini 2.5 Flash.

    Using lru_cache ensures the model is only instantiated once regardless
    of how many times get_llm() is called across the codebase.
    """
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.7,
    )
