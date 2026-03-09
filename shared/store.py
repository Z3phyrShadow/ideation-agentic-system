"""
store.py
--------
Manages SQLite persistence for Discord thread IDs and scored project ideas.

Tables:
    bot_threads   — thread IDs created by the bot (unchanged)
    ideas         — scored project ideas with portfolio and activity scores
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
    Safe to call on every startup.
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
                last_scored_at   TEXT    NOT NULL DEFAULT ''
            )
            """
        )
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


async def get_top_idea() -> Optional[dict]:
    """Return the highest-scoring idea, or None if no ideas are scored yet."""
    ideas = await load_ideas()
    return ideas[0] if ideas else None


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
