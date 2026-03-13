"""
build.py (tools)
----------------
Build-phase tool available to the ideation agent.

When the user decides to start building, the agent calls queue_build_task()
to enqueue a task for Antigravity to pick up and scaffold via MCP.

Uses sqlite3 (sync) deliberately — this tool runs inside a ThreadPoolExecutor
thread (via asyncio.to_thread), so async DB calls would require a separate
event loop. Sync sqlite3 is the correct choice here.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

log = logging.getLogger("tools.build")

_DB_PATH = str(Path(__file__).parent.parent / "threads.db")


@tool
def queue_build_task(idea_title: str, idea_summary: str, stack_hint: str = "") -> str:
    """
    Queue this idea for project initialization via Antigravity.

    Call this ONLY when the user explicitly says they want to start building
    (e.g. "let's go", "I want to build this", "start the project").

    Antigravity will scaffold a GitHub repo, create issues from the discussion,
    and push an initial commit. The user will be notified when it's ready.

    Args:
        idea_title:   Short title of the idea (used as the repo name).
        idea_summary: 2-3 sentence description of what to build.
        stack_hint:   Optional tech stack hint (e.g. "FastAPI + SQLite + React").
                      Leave empty if not discussed.

    Returns:
        Confirmation message to relay to the user.
    """
    thread_id: int = getattr(queue_build_task, "_current_thread_id", 0)
    now_iso = datetime.now(timezone.utc).isoformat()

    log.info("[tool] queue_build_task: %r (thread=%d)", idea_title, thread_id)

    try:
        con = sqlite3.connect(_DB_PATH)
        try:
            cur = con.execute(
                """
                INSERT INTO build_tasks
                    (thread_id, idea_title, idea_summary, stack_hint, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (thread_id, idea_title, idea_summary, stack_hint, now_iso),
            )
            task_id = cur.lastrowid
            con.commit()
        finally:
            con.close()

        log.info("[tool] Build task queued: id=%d thread=%d", task_id, thread_id)
        return (
            f"✅ Build task queued (id={task_id})! "
            f"Open this project in Antigravity — it will scaffold **{idea_title}** "
            f"and report back when the repo is ready. "
            f"🚀 Go forth, padawan — your destiny awaits."
        )

    except Exception as exc:
        log.exception("[tool] queue_build_task failed")
        return f"[Failed to queue build task: {exc}]"


@tool
def mark_project_done(reason: str = "") -> str:
    """
    Mark this project as completed and stop all tracking.

    Call this when the user says the project is done, shipped, finished, or complete.
    This stops GitHub commit polling and removes the idea from the morning brief.

    Args:
        reason: Optional one-line note on what was shipped (e.g. "Published to PyPI").

    Returns:
        Confirmation message to relay to the user.
    """
    thread_id: int = getattr(mark_project_done, "_current_thread_id", 0)
    log.info("[tool] mark_project_done: thread=%d reason=%r", thread_id, reason)

    try:
        con = sqlite3.connect(_DB_PATH)
        try:
            con.execute(
                "UPDATE ideas SET status = 'done' WHERE thread_id = ?",
                (thread_id,),
            )
            con.commit()
        finally:
            con.close()

        log.info("[tool] Idea thread=%d marked as done.", thread_id)
        suffix = f" ({reason})" if reason else ""
        return (
            f"✅ Project marked as complete{suffix}! "
            f"Tracking stopped — it won't appear in the morning brief anymore. "
            f"🎉 Congrats on shipping!"
        )

    except Exception as exc:
        log.exception("[tool] mark_project_done failed")
        return f"[Failed to mark project done: {exc}]"
