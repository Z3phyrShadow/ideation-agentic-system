"""
web_search.py
-------------
Tavily web search tool — available to all agents.
"""

import logging

from langchain_core.tools import tool

log = logging.getLogger("tools.web_search")

_SEARCH_MAX_RESULTS: int = 3


@tool
def search_web(query: str) -> str:
    """
    Search the web for current information and return the top results.

    Use this tool ONLY when:
    - The user explicitly asks you to search, research, or look something up.
    - The question requires up-to-date information that your training data
      may not contain (e.g. recent news, current prices, live events).
    - Do NOT call this for general knowledge questions you can answer directly.

    After getting results, call fetch_url on the most relevant result URL
    to read the full content before forming your response.

    Args:
        query: A concise, specific search query (as you'd type into Google).

    Returns:
        Numbered list of results with title, URL, and a short snippet each.
        Returns an error string if the search fails.
    """
    log.info("[tool] search_web: %r", query)
    try:
        from tavily import TavilyClient
        from discord_bot.config import TAVILY_API_KEY

        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=query,
            max_results=_SEARCH_MAX_RESULTS,
            search_depth="basic",
        )

        results = response.get("results", [])
        if not results:
            return "[No results found for this query.]"

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            url = r.get("url", "")
            snippet = r.get("content", "").strip()[:300]
            lines.append(f"{i}. **{title}**\n   URL: {url}\n   {snippet}")

        return "\n\n".join(lines)

    except Exception as exc:
        log.exception("[tool] search_web failed")
        return f"[Web search failed: {exc}]"
