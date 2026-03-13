"""
store.py
--------
Manages SQLite persistence for Discord thread IDs, scored ideas, career profiles,
and the MCP build task queue.

Tables:
    bot_threads   — thread IDs created by the bot
    ideas         — scored ideas with lifecycle status and optional GitHub repo URL
    career_profiles
    build_tasks   — MCP task queue (pending → in_progress → done)
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

_DB_PATH: Path = Path(__file__).parent.parent / "threads.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """
    Initialize the SQLite database and create all tables if they don't exist.
    Safe to call on every startup; uses ALTER TABLE to add new columns to
    existing databases without data loss.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_threads (
                thread_id INTEGER PRIMARY KEY
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ideas (
                thread_id        INTEGER PRIMARY KEY,
                title            TEXT    NOT NULL DEFAULT '',
                summary          TEXT    NOT NULL DEFAULT '',
                portfolio_score  REAL    NOT NULL DEFAULT 0.0,
                activity_score   REAL    NOT NULL DEFAULT 0.0,
                combined_score   REAL    NOT NULL DEFAULT 0.0,
                last_active_at   TEXT    NOT NULL DEFAULT '',
                last_scored_at   TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT 'ideating',
                repo_url         TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        # Add new columns to existing DB if upgrading from older schema
        for col, definition in [
            ("status",   "TEXT NOT NULL DEFAULT 'ideating'"),
            ("repo_url", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE ideas ADD COLUMN {col} {definition}")
            except Exception:
                pass  # column already exists

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS career_profiles (
                user_id      INTEGER PRIMARY KEY,
                role_target  TEXT    NOT NULL DEFAULT '',
                skills_json  TEXT    NOT NULL DEFAULT '[]',
                last_updated TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS build_tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id    INTEGER NOT NULL,
                idea_title   TEXT    NOT NULL DEFAULT '',
                idea_summary TEXT    NOT NULL DEFAULT '',
                stack_hint   TEXT    NOT NULL DEFAULT '',
                status       TEXT    NOT NULL DEFAULT 'pending',
                created_at   TEXT    NOT NULL DEFAULT '',
                completed_at TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        await db.commit()


# ---------------------------------------------------------------------------
# bot_threads operations (unchanged)
# ---------------------------------------------------------------------------


async def save_thread(thread_id: int) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO bot_threads (thread_id) VALUES (?)",
            (thread_id,),
        )
        await db.commit()


async def load_threads() -> set[int]:
    async with aiosqlite.connect(_DB_PATH) as db:
        cursor = await db.execute("SELECT thread_id FROM bot_threads")
        rows = await cursor.fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# ideas operations
# ---------------------------------------------------------------------------


async def upsert_idea(
    thread_id: int,
    title: str,
    summary: str,
    portfolio_score: float,
    activity_score: float,
    last_active_at: datetime,
) -> None:
    """
    Insert or update a scored idea record.

    combined_score = 60% portfolio + 40% activity (both on 1–10 scale).
    """
    combined = round(0.6 * portfolio_score + 0.4 * activity_score, 2)
    now_iso = datetime.now(timezone.utc).isoformat()
    active_iso = last_active_at.isoformat()

    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO ideas
                (thread_id, title, summary, portfolio_score, activity_score,
                 combined_score, last_active_at, last_scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                title           = excluded.title,
                summary         = excluded.summary,
                portfolio_score = excluded.portfolio_score,
                activity_score  = excluded.activity_score,
                combined_score  = excluded.combined_score,
                last_active_at  = excluded.last_active_at,
                last_scored_at  = excluded.last_scored_at
            """,
            (thread_id, title, summary, portfolio_score,
             activity_score, combined, active_iso, now_iso),
        )
        await db.commit()


async def load_ideas() -> list[dict]:
    """Return all ideas ordered by combined_score descending."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ideas ORDER BY combined_score DESC"
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def load_ideating_ideas() -> list[dict]:
    """Return ideas still in the ideation phase (no repo yet), ranked by score."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ideas WHERE status = 'ideating' ORDER BY combined_score DESC"
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def load_building_ideas() -> list[dict]:
    """Return ideas currently being built (have a linked repo)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM ideas WHERE status = 'building' AND repo_url != ''"
        )
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_top_idea() -> Optional[dict]:
    """Return the highest-scoring ideating idea, or None."""
    ideas = await load_ideating_ideas()
    return ideas[0] if ideas else None


async def set_idea_building(thread_id: int, repo_url: str) -> None:
    """Mark an idea as building and store its GitHub repo URL."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE ideas SET status = 'building', repo_url = ? WHERE thread_id = ?",
            (repo_url, thread_id),
        )
        await db.commit()


async def update_idea_activity(thread_id: int, activity_score: float) -> None:
    """Update only the activity score for a building idea (GitHub commits)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        # Recalculate combined score preserving last portfolio score
        cursor = await db.execute(
            "SELECT portfolio_score FROM ideas WHERE thread_id = ?", (thread_id,)
        )
        row = await cursor.fetchone()
        if row:
            portfolio_score = row[0]
            combined = round(0.6 * portfolio_score + 0.4 * activity_score, 2)
            await db.execute(
                """
                UPDATE ideas
                SET activity_score = ?, combined_score = ?, last_scored_at = ?
                WHERE thread_id = ?
                """,
                (activity_score, combined, datetime.now(timezone.utc).isoformat(), thread_id),
            )
            await db.commit()


# ---------------------------------------------------------------------------
# career_profiles operations
# ---------------------------------------------------------------------------


async def upsert_career_profile(
    user_id: int,
    role_target: str,
    skills_json: str,
) -> None:
    """Insert or update a user's career profile."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO career_profiles (user_id, role_target, skills_json, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                role_target  = excluded.role_target,
                skills_json  = excluded.skills_json,
                last_updated = excluded.last_updated
            """,
            (user_id, role_target, skills_json, now_iso),
        )
        await db.commit()


async def get_career_profile(user_id: int) -> Optional[dict]:
    """Return a user's career profile, or None if not set up yet."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM career_profiles WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# build_tasks operations (MCP task queue)
# ---------------------------------------------------------------------------


async def queue_build_task(
    thread_id: int,
    idea_title: str,
    idea_summary: str,
    stack_hint: str = "",
) -> int:
    """Add a new build task to the queue. Returns the new task ID."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO build_tasks
                (thread_id, idea_title, idea_summary, stack_hint, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (thread_id, idea_title, idea_summary, stack_hint, now_iso),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_pending_task() -> Optional[dict]:
    """
    Return the oldest pending task and mark it in_progress.
    Returns None if the queue is empty.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM build_tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        task = dict(row)
        await db.execute(
            "UPDATE build_tasks SET status = 'in_progress' WHERE id = ?",
            (task["id"],),
        )
        await db.commit()
    return task


async def complete_build_task(task_id: int, repo_url: str) -> Optional[int]:
    """
    Mark a build task as done, store the repo URL on the idea, flip idea status.
    Returns the thread_id so the caller can send a Discord message.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT thread_id FROM build_tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        thread_id = row["thread_id"]
        await db.execute(
            "UPDATE build_tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (now_iso, task_id),
        )
        await db.execute(
            "UPDATE ideas SET status = 'building', repo_url = ? WHERE thread_id = ?",
            (repo_url, thread_id),
        )
        await db.commit()
    return thread_id
