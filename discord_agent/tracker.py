"""
tracker.py
----------
Periodic tracker agent that scores project idea threads and triggers
Google Calendar reminders for the top idea.

Public API:
    score_thread(thread)             — scores a single Discord thread
    run_tracker(client, bot_threads) — scores all tracked threads, updates DB + Calendar
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord

from discord_agent import store
from discord_agent.llm import get_llm

log = logging.getLogger("discord_agent.tracker")

# ---------------------------------------------------------------------------
# Scorer prompt — loaded once at module import
# ---------------------------------------------------------------------------

_SCORER_PROMPT_PATH = Path(__file__).parent / "scorer_prompt.txt"
_SCORER_PROMPT: str = _SCORER_PROMPT_PATH.read_text(encoding="utf-8").strip()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HISTORY_LIMIT: int = 50          # messages fetched per thread for scoring
_ACTIVITY_MAX_MESSAGES: int = 30  # message count cap for volume sub-score
_ACTIVITY_WINDOW_DAYS: int = 7    # recency window for momentum sub-score
_RECENCY_DECAY_DAYS: int = 14     # days after which recency score bottoms out


# ---------------------------------------------------------------------------
# Portfolio score (LLM)
# ---------------------------------------------------------------------------


def _parse_scorer_json(raw: str) -> Optional[dict]:
    """
    Extract a JSON object from the LLM output.

    The model is instructed to return bare JSON, but may occasionally wrap it
    in markdown code fences. This function handles both cases robustly.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: find the first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    log.warning("[tracker] Failed to parse scorer JSON from LLM output: %r", raw[:200])
    return None


def score_portfolio_worthiness(thread_text: str) -> tuple[float, str, str]:
    """
    Ask the LLM to evaluate a thread as a portfolio project.

    Args:
        thread_text: Concatenated text content of the thread's messages.

    Returns:
        (score, title, summary) where score is 1.0–10.0.
        Falls back to (5.0, "Untitled Idea", "") on parse failure.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = get_llm()
    messages = [
        SystemMessage(content=_SCORER_PROMPT),
        HumanMessage(content=thread_text),
    ]

    try:
        result = llm.invoke(messages)
        raw = str(result.content).strip()
        data = _parse_scorer_json(raw)

        if data:
            score = float(data.get("score", 5.0))
            score = max(1.0, min(10.0, score))   # clamp to [1, 10]
            title = str(data.get("title", "Untitled Idea")).strip()
            summary = str(data.get("summary", "")).strip()
            rationale = str(data.get("rationale", "")).strip()
            log.info(
                "[tracker] portfolio_score=%.1f title=%r rationale=%s",
                score, title, rationale,
            )
            return score, title, summary

    except Exception:
        log.exception("[tracker] score_portfolio_worthiness failed")

    return 5.0, "Untitled Idea", ""


# ---------------------------------------------------------------------------
# Activity score (pure math, no LLM)
# ---------------------------------------------------------------------------


def compute_activity_score(messages: list[discord.Message]) -> tuple[float, datetime]:
    """
    Compute an activity score (1–10) from message metadata alone.

    Sub-scores:
        Recency  (40%): decays linearly from 10 → 1 over _RECENCY_DECAY_DAYS
        Volume   (30%): capped count, normalised to [1, 10]
        Momentum (30%): messages in the last 7 days, normalised to [1, 10]

    Args:
        messages: List of discord.Message objects (any order).

    Returns:
        (activity_score, last_active_at) where last_active_at is UTC-aware.
    """
    if not messages:
        now = datetime.now(timezone.utc)
        return 1.0, now

    now = datetime.now(timezone.utc)

    # Ensure all timestamps are UTC-aware
    timestamps = [
        m.created_at.replace(tzinfo=timezone.utc)
        if m.created_at.tzinfo is None
        else m.created_at
        for m in messages
    ]
    last_active = max(timestamps)

    # --- Recency sub-score ---
    days_ago = (now - last_active).total_seconds() / 86_400
    recency = max(1.0, 10.0 - (days_ago / _RECENCY_DECAY_DAYS) * 9.0)

    # --- Volume sub-score ---
    count = min(len(messages), _ACTIVITY_MAX_MESSAGES)
    volume = 1.0 + (count / _ACTIVITY_MAX_MESSAGES) * 9.0

    # --- Momentum sub-score ---
    cutoff = now - timedelta(days=_ACTIVITY_WINDOW_DAYS)
    recent_count = sum(1 for ts in timestamps if ts >= cutoff)
    momentum = 1.0 + min(recent_count, 10) * 0.9   # 0 msgs→1.0, 10 msgs→10.0

    score = 0.40 * recency + 0.30 * volume + 0.30 * momentum
    score = round(max(1.0, min(10.0, score)), 2)

    log.debug(
        "[tracker] activity recency=%.2f volume=%.2f momentum=%.2f → %.2f",
        recency, volume, momentum, score,
    )
    return score, last_active


# ---------------------------------------------------------------------------
# Per-thread scorer
# ---------------------------------------------------------------------------


async def score_thread(thread: discord.Thread) -> Optional[dict]:
    """
    Fetch a thread's messages and compute both scores.

    Returns a dict ready to be passed to store.upsert_idea(), or None if the
    thread has no readable content.
    """
    raw_messages: list[discord.Message] = []
    try:
        async for msg in thread.history(limit=_HISTORY_LIMIT):
            raw_messages.append(msg)
    except discord.Forbidden:
        log.warning("[tracker] No access to thread %d, skipping.", thread.id)
        return None
    except Exception:
        log.exception("[tracker] Failed to fetch history for thread %d", thread.id)
        return None

    if not raw_messages:
        log.info("[tracker] Thread %d is empty, skipping.", thread.id)
        return None

    # Build plain text for LLM — concatenate non-empty, non-bot-only messages
    lines = [
        f"{m.author.display_name}: {m.content}"
        for m in reversed(raw_messages)   # chronological
        if m.content.strip()
    ]
    if not lines:
        return None

    thread_text = "\n".join(lines)

    # Run LLM call in thread pool (synchronous call)
    portfolio_score, title, summary = await asyncio.to_thread(
        score_portfolio_worthiness, thread_text
    )
    activity_score, last_active_at = compute_activity_score(raw_messages)

    log.info(
        "[tracker] Thread %d → portfolio=%.1f activity=%.2f title=%r",
        thread.id, portfolio_score, activity_score, title,
    )

    return {
        "thread_id":       thread.id,
        "title":           title,
        "summary":         summary,
        "portfolio_score": portfolio_score,
        "activity_score":  activity_score,
        "last_active_at":  last_active_at,
    }


# ---------------------------------------------------------------------------
# Bulk tracker run
# ---------------------------------------------------------------------------


async def run_tracker(
    client: discord.Client,
    bot_threads: set[int],
) -> None:
    """
    Score all tracked threads, persist results, and update the Calendar reminder.

    Args:
        client:      The live discord.Client (needed to resolve thread objects).
        bot_threads: Set of thread IDs owned by the bot.
    """
    if not bot_threads:
        log.info("[tracker] No threads to score.")
        return

    log.info("[tracker] Starting scoring run for %d threads.", len(bot_threads))
    scored = 0

    for thread_id in list(bot_threads):
        thread: Optional[discord.Thread] = client.get_channel(thread_id)  # type: ignore[assignment]

        if thread is None:
            # Thread not in cache — try fetching
            try:
                thread = await client.fetch_channel(thread_id)  # type: ignore[assignment]
            except Exception:
                log.warning("[tracker] Could not fetch thread %d, skipping.", thread_id)
                continue

        if not isinstance(thread, discord.Thread):
            log.warning("[tracker] Channel %d is not a Thread, skipping.", thread_id)
            continue

        record = await score_thread(thread)
        if record is None:
            continue

        try:
            await store.upsert_idea(**record)
            scored += 1
        except Exception:
            log.exception("[tracker] Failed to upsert idea for thread %d", thread_id)

    log.info("[tracker] Scored %d/%d threads.", scored, len(bot_threads))

    # Update Google Calendar with the top idea
    top = await store.get_top_idea()
    if top:
        _trigger_calendar_update(top)
    else:
        log.info("[tracker] No ideas in DB yet; skipping Calendar update.")


def _trigger_calendar_update(idea: dict) -> None:
    """Fire the Calendar upsert synchronously (it's fast and non-blocking enough)."""
    try:
        from discord_agent import calendar_client
        calendar_client.upsert_7pm_reminder(
            idea_title=idea["title"],
            idea_summary=idea["summary"] or idea["title"],
        )
    except Exception:
        log.exception("[tracker] Google Calendar update failed")
