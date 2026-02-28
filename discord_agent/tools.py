"""
tools.py
--------
LangChain tool definitions for document ingestion and web research.

Tools:
    search_web        — Search the web via Tavily and return ranked results.
    fetch_url         — Fetch a URL and return clean article text (trafilatura).
    read_attachment   — Download a Discord CDN file and extract its text.
    summarize_document — Chunk and summarize a large block of text.
"""

import io
import logging
from typing import Optional

import httpx
from langchain_core.tools import tool

log = logging.getLogger("discord_agent.tools")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT: float = 15.0
_MAX_TEXT_CHARS: int = 8_000
_SUMMARIZE_THRESHOLD: int = 6_000
_CHUNK_SIZE: int = 4_000
_SEARCH_MAX_RESULTS: int = 3        # keep low to conserve Tavily credits

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".rst", ".csv",
    ".pdf",
})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _html_to_text(html: str, url: str = "") -> str:
    """
    Extract clean article text from HTML using trafilatura.
    Falls back to a simple tag-strip if trafilatura returns nothing.
    """
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            url=url or None,
        )
        if text:
            return text
    except Exception as exc:
        log.warning("trafilatura failed (%s), falling back to tag-strip", exc)

    # Fallback: crude regex strip
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_pdf_text(data: bytes) -> str:
    """Extract plain text from PDF bytes using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except ImportError:
        return "[PDF extraction unavailable: pypdf not installed]"
    except Exception as exc:
        return f"[PDF extraction failed: {exc}]"


def _truncate(text: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Truncate text and append a notice if it was cut."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[…content truncated at {max_chars} chars]"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


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
        from discord_agent.config import TAVILY_API_KEY

        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(
            query=query,
            max_results=_SEARCH_MAX_RESULTS,
            search_depth="basic",   # "basic" uses fewer credits than "advanced"
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


@tool
def fetch_url(url: str) -> str:
    """
    Fetch the content of a web URL and return it as clean readable text.

    Use this tool whenever the user mentions or pastes a URL, or after
    search_web returns a result you want to read in full.

    Args:
        url: The full URL to fetch (must start with http:// or https://).

    Returns:
        Plain-text content of the page, truncated to ~8 000 characters.
        Returns an error string if the request fails.
    """
    log.info("[tool] fetch_url: %s", url)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (ideation-bot/1.0)"},
            )
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type or url.lower().split("?")[0].endswith(".pdf"):
            text = _extract_pdf_text(response.content)
        elif "html" in content_type or "text" in content_type:
            text = _html_to_text(response.text, url=url)
        else:
            text = response.text

        if not text or not text.strip():
            return "[The page returned no readable text content.]"

        return _truncate(text)

    except httpx.HTTPStatusError as exc:
        return f"[HTTP error {exc.response.status_code} fetching {url}]"
    except httpx.RequestError as exc:
        return f"[Request failed for {url}: {exc}]"
    except Exception as exc:
        return f"[Unexpected error fetching {url}: {exc}]"


@tool
def read_attachment(url: str, filename: Optional[str] = None) -> str:
    """
    Download a file from a Discord CDN URL and extract its text content.

    Use this tool when the user shares a file attachment (.txt, .md, .pdf,
    .csv, .rst) in the thread. The attachment URL format is typically:
        https://cdn.discordapp.com/attachments/...

    Args:
        url:      Direct download URL for the attachment.
        filename: Optional filename hint used to detect the file type.

    Returns:
        Extracted text content, truncated to ~8 000 characters.
    """
    log.info("[tool] read_attachment: %s (filename=%s)", url, filename)

    name = filename or url.split("?")[0].split("/")[-1]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ext and ext not in _SUPPORTED_EXTENSIONS:
        return (
            f"[Unsupported file type '{ext}'. "
            f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}]"
        )

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()

        if ext == ".pdf":
            text = _extract_pdf_text(response.content)
        else:
            text = response.text

        if not text.strip():
            return "[The attachment contained no readable text.]"

        return _truncate(text)

    except httpx.HTTPStatusError as exc:
        return f"[HTTP error {exc.response.status_code} downloading attachment]"
    except httpx.RequestError as exc:
        return f"[Request failed downloading attachment: {exc}]"
    except Exception as exc:
        return f"[Unexpected error reading attachment: {exc}]"


@tool
def summarize_document(text: str) -> str:
    """
    Summarize a large block of text that is too long to reason over directly.

    Use this when text from fetch_url or read_attachment exceeds ~6 000 chars.
    It chunks the text and returns a condensed version preserving key content.

    Args:
        text: The raw document text to summarize.

    Returns:
        A concise summary preserving the main ideas, arguments, and data points.
    """
    log.info("[tool] summarize_document: %d chars input", len(text))

    from discord_agent.llm import get_llm
    from langchain_core.messages import HumanMessage

    llm = get_llm()

    if len(text) <= _SUMMARIZE_THRESHOLD:
        chunks = [text]
    else:
        chunks = []
        while len(text) > _CHUNK_SIZE:
            split_at = text.rfind("\n\n", 0, _CHUNK_SIZE)
            if split_at == -1:
                split_at = text.rfind(" ", 0, _CHUNK_SIZE)
            if split_at == -1:
                split_at = _CHUNK_SIZE
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip()
        if text:
            chunks.append(text)

    log.info("[tool] summarize_document: %d chunks", len(chunks))

    chunk_summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        prompt = (
            f"Summarize the following text excerpt (part {i + 1} of {len(chunks)}) "
            "concisely, preserving key facts, arguments, and data:\n\n"
            f"{chunk}"
        )
        result = llm.invoke([HumanMessage(content=prompt)])
        chunk_summaries.append(str(result.content))

    if len(chunk_summaries) == 1:
        return chunk_summaries[0]

    merge_prompt = (
        "The following are partial summaries of a longer document. "
        "Produce a single, coherent, concise summary that captures the whole:\n\n"
        + "\n\n---\n\n".join(chunk_summaries)
    )
    final = llm.invoke([HumanMessage(content=merge_prompt)])
    return str(final.content)


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ALL_TOOLS = [search_web, fetch_url, read_attachment, summarize_document]
