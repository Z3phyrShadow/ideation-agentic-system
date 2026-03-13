"""
server.py (mcp_server)
----------------------
Proper MCP server using the official `mcp` Python SDK with SSE transport.

Antigravity connects to this server via mcp_config.json at:
    http://127.0.0.1:8765/sse

The server exposes two tools that Antigravity auto-discovers via tools/list:
    get_next_task         — fetch the next pending build task from the queue
    report_task_complete  — notify the bot that a repo has been scaffolded

The server runs as a background asyncio task alongside the Discord bot.
"""

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("mcp_server.server")

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("ideation-system")

# Async callback set by main.py — called when a build task completes
_on_task_complete = None


def set_completion_callback(callback):
    """Register an async callback invoked when a build task completes."""
    global _on_task_complete
    _on_task_complete = callback


# ---------------------------------------------------------------------------
# Tool: get_next_task
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_next_task() -> dict:
    """
    Return the next pending build task from the ideation system's queue.

    Antigravity should poll this continuously. When a task is returned,
    scaffold the GitHub repo, push an initial commit, create issues from the
    idea summary, then call report_task_complete with the repo URL.

    Returns a dict with:
        status: "ok" | "no_tasks"
        task (when status is "ok"):
            id:           int   — task ID (required for report_task_complete)
            idea_title:   str   — use as the GitHub repo name
            idea_summary: str   — use to populate README and project description
            stack_hint:   str   — suggested tech stack (may be empty)
    """
    from shared import store

    task = await store.get_pending_task()
    if task is None:
        log.debug("[mcp] get_next_task → no pending tasks")
        return {"status": "no_tasks"}

    log.info("[mcp] Dispatching task id=%d title=%r", task["id"], task["idea_title"])
    return {
        "status": "ok",
        "task": {
            "id": task["id"],
            "idea_title": task["idea_title"],
            "idea_summary": task["idea_summary"],
            "stack_hint": task["stack_hint"],
        },
    }


# ---------------------------------------------------------------------------
# Tool: report_task_complete
# ---------------------------------------------------------------------------


@mcp.tool()
async def report_task_complete(task_id: int, repo_url: str) -> dict:
    """
    Report that a build task has been completed (repo scaffolded and pushed).

    Call this after you have:
      1. Created the GitHub repo
      2. Committed the initial project structure
      3. Created GitHub issues from the idea summary

    Args:
        task_id:  The ID returned by get_next_task.
        repo_url: Full GitHub URL of the repo (e.g. https://github.com/you/repo).

    Returns:
        {"status": "ok"} on success.
    """
    from shared import store

    thread_id = await store.complete_build_task(task_id, repo_url)
    if thread_id is None:
        log.warning("[mcp] report_task_complete: task_id=%d not found", task_id)
        return {"status": "error", "detail": "task not found"}

    log.info("[mcp] Task %d complete — repo=%s (thread=%d)", task_id, repo_url, thread_id)

    if _on_task_complete:
        asyncio.create_task(_on_task_complete(thread_id, repo_url))

    return {"status": "ok", "thread_id": thread_id}


# ---------------------------------------------------------------------------
# Tool: mark_project_complete
# ---------------------------------------------------------------------------


@mcp.tool()
async def mark_project_complete(task_id: int) -> dict:
    """
    Mark a project as done — stops GitHub commit tracking and morning brief inclusion.

    Call this when the project has been shipped/published.

    Args:
        task_id: The build task ID linked to the idea (returned by get_next_task).
    """
    from shared import store
    import aiosqlite
    from pathlib import Path

    async with aiosqlite.connect(Path("threads.db")) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT thread_id FROM build_tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()

    if row is None:
        return {"status": "error", "detail": "task not found"}

    await store.mark_idea_done(row["thread_id"])
    log.info("[mcp] Marked idea thread=%d as done (task_id=%d)", row["thread_id"], task_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Server lifecycle (run as background asyncio task from main.py)
# ---------------------------------------------------------------------------


async def run_server(port: int = 8765) -> None:
    """Start the FastMCP SSE server as a background asyncio task."""
    import uvicorn

    # FastMCP.sse_app() returns a Starlette ASGI app with /sse and /messages/ endpoints
    sse_app = mcp.sse_app()

    config = uvicorn.Config(
        sse_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    log.info("[mcp] MCP SSE server starting on http://127.0.0.1:%d/sse", port)
    await server.serve()
