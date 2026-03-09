# Shared tools package — used across all agents
from tools.web_search import search_web
from tools.document import fetch_url, read_attachment, summarize_document

ALL_TOOLS = [search_web, fetch_url, read_attachment, summarize_document]
