# Shared tools package — used across all agents
from tools.web_search import search_web
from tools.document import fetch_url, read_attachment, summarize_document
from tools.build import queue_build_task, mark_project_done

ALL_TOOLS = [search_web, fetch_url, read_attachment, summarize_document, queue_build_task, mark_project_done]
