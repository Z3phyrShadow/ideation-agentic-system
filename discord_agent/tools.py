"""
tools.py
--------
LangChain tool definitions for document ingestion and understanding.

Tools:
    fetch_url         — Fetch a URL and return readable plain text.
    read_attachment   — Download a Discord CDN file and extract its text.
    summarize_document — Chunk and summarize a large block of text.
"""

import io
import logging
import re
from typing import Optional

import httpx
from langchain_core.tools import tool

log = logging.getLogger("discord_agent.tools")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT: float = 15.0          # seconds
_MAX_TEXT_CHARS: int = 8_000         # max chars returned to the agent raw
_SUMMARIZE_THRESHOLD: int = 6_000    # chars above which summarize_document is useful
_CHUNK_SIZE: int = 4_000             # chars per chunk when chunking for summary

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".rst", ".csv",   # plain text variants
    ".pdf",                           # PDF (via pypdf)
})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace to produce readable plain text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove script / style noise.
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
    except ImportError:
        # Fallback: crude regex strip.
        text = re.sub(r"<[^>]+>", " ", html)

    # Collapse excessive whitespace.
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text


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
def fetch_url(url: str) -> str:
    """
    Fetch the content of a web URL and return it as readable plain text.

    Use this tool whenever the user mentions or pastes a URL in their message
    and you need to understand what the page contains before responding.

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
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            text = _extract_pdf_text(response.content)
        elif "html" in content_type:
            text = _html_to_text(response.text)
        else:
            text = response.text

        if not text.strip():
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

    Use this tool when the user shares a file attachment (.txt, .md, .pdf, .csv, .rst)
    in the thread. The attachment URL format is typically:
        https://cdn.discordapp.com/attachments/...

    Supported formats: .txt, .md, .rst, .csv (plain text) and .pdf.

    Args:
        url:      Direct download URL for the attachment.
        filename: Optional filename hint used to detect the file type.
                  If omitted, the extension is inferred from the URL.

    Returns:
        Extracted text content, truncated to ~8 000 characters.
        Returns an error string if the file cannot be read.
    """
    log.info("[tool] read_attachment: %s (filename=%s)", url, filename)

    # Determine extension.
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

    Use this tool when extracted document text exceeds roughly 6 000 characters.
    It splits the text into chunks, summarizes each, and returns a condensed
    version you can reason over without losing the key information.

    Args:
        text: The raw document text to summarize.

    Returns:
        A concise summary preserving the main ideas, arguments, and data points.
    """
    log.info("[tool] summarize_document: %d chars input", len(text))

    # Lazy import to avoid circulars — tools.py is imported by graph.py which
    # imports llm.py, so we import inside the function call.
    from discord_agent.llm import get_llm

    llm = get_llm()

    if len(text) <= _SUMMARIZE_THRESHOLD:
        # Short enough to summarize in one shot.
        chunks = [text]
    else:
        # Split on paragraph boundaries where possible.
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
        from langchain_core.messages import HumanMessage
        result = llm.invoke([HumanMessage(content=prompt)])
        chunk_summaries.append(str(result.content))

    if len(chunk_summaries) == 1:
        return chunk_summaries[0]

    # Merge chunk summaries into a final consolidated summary.
    merge_prompt = (
        "The following are partial summaries of a longer document. "
        "Produce a single, coherent, concise summary that captures the whole:\n\n"
        + "\n\n---\n\n".join(chunk_summaries)
    )
    from langchain_core.messages import HumanMessage
    final = llm.invoke([HumanMessage(content=merge_prompt)])
    return str(final.content)


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ALL_TOOLS = [fetch_url, read_attachment, summarize_document]
