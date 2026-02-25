"""
store.py
--------
Manages SQLite persistence for Discord thread IDs created by the bot.

Design:
  - Uses aiosqlite for non-blocking async I/O on the Discord event loop.
  - The database file (threads.db) is created in the project root on first run.
  - Only three operations are needed:
      init_db()           — create the table if it doesn't exist
      save_thread(id)     — insert a new thread ID
      load_threads()      — return all stored IDs as a set

  - All DB interaction happens only at startup (load) and on new thread
    creation (save). Per-message lookups use the in-memory set in main.py.
"""

from pathlib import Path

import aiosqlite

# Resolve the DB path relative to this file's parent package directory.
_DB_PATH: Path = Path(__file__).parent.parent / "threads.db"


async def init_db() -> None:
    """
    Initialize the SQLite database and create the bot_threads table if
    it does not already exist.

    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_threads (
                thread_id INTEGER PRIMARY KEY
            )
            """
        )
        await db.commit()


async def save_thread(thread_id: int) -> None:
    """
    Persist a new thread ID to the database.

    Uses INSERT OR IGNORE to safely handle duplicate calls without raising.

    Args:
        thread_id: The Discord thread ID to persist.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO bot_threads (thread_id) VALUES (?)",
            (thread_id,),
        )
        await db.commit()


async def load_threads() -> set[int]:
    """
    Load all persisted thread IDs from the database.

    Returns:
        A set of integer thread IDs previously created by the bot.
        Returns an empty set if the database has no records yet.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        cursor = await db.execute("SELECT thread_id FROM bot_threads")
        rows = await cursor.fetchall()
    return {row[0] for row in rows}
