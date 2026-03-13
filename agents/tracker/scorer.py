"""
scorer.py (tracker)
-------------------
Periodic tracker pipeline that scores Discord idea threads.

Public API:
    score_thread(thread)             — scores a single Discord thread
    run_tracker(client, bot_threads) — scores all tracked threads, updates DB
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord

from shared import store
from shared.llm import get_llm

log = logging.getLogger("agents.tracker.scorer")

_SCORER_PROMPT_PATH = Path(__file__).parent / "scorer_prompt.txt"
_SCORER_PROMPT: str = _SCORER_PROMPT_PATH.read_text(encoding="utf-8").strip()

_HISTORY_LIMIT: int = 50
_ACTIVITY_MAX_MESSAGES: int = 30
_ACTIVITY_WINDOW_DAYS: int = 7
_RECENCY_DECAY_DAYS: int = 14


def _parse_scorer_json(raw: str) -> Optional[dict]:
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    log.warning("[tracker] Failed to parse scorer JSON: %r", raw[:200])
    return None


def score_portfolio_worthiness(thread_text: str) -> tuple[float, str, str]:
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = get_llm()
    try:
        result = llm.invoke([
            SystemMessage(content=_SCORER_PROMPT),
            HumanMessage(content=thread_text),
        ])
        raw = str(result.content).strip()
        data = _parse_scorer_json(raw)
        if data:
            score = float(data.get("score", 5.0))
            score = max(1.0, min(10.0, score))
            title = str(data.get("title", "Untitled Idea")).strip()
            summary = str(data.get("summary", "")).strip()
            rationale = str(data.get("rationale", "")).strip()
            log.info("[tracker] portfolio_score=%.1f title=%r rationale=%s", score, title, rationale)
            return score, title, summary
    except Exception:
        log.exception("[tracker] score_portfolio_worthiness failed")
    return 5.0, "Untitled Idea", ""


def compute_activity_score(messages: list[discord.Message]) -> tuple[float, datetime]:
    if not messages:
        return 1.0, datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)
    timestamps = [
        m.created_at.replace(tzinfo=timezone.utc) if m.created_at.tzinfo is None else m.created_at
        for m in messages
    ]
    last_active = max(timestamps)

    days_ago = (now - last_active).total_seconds() / 86_400
    recency = max(1.0, 10.0 - (days_ago / _RECENCY_DECAY_DAYS) * 9.0)

    count = min(len(messages), _ACTIVITY_MAX_MESSAGES)
    volume = 1.0 + (count / _ACTIVITY_MAX_MESSAGES) * 9.0

    cutoff = now - timedelta(days=_ACTIVITY_WINDOW_DAYS)
    recent_count = sum(1 for ts in timestamps if ts >= cutoff)
    momentum = 1.0 + min(recent_count, 10) * 0.9

    score = round(max(1.0, min(10.0, 0.40 * recency + 0.30 * volume + 0.30 * momentum)), 2)
    return score, last_active


async def score_thread(thread: discord.Thread) -> Optional[dict]:
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
        return None

    lines = [
        f"{m.author.display_name}: {m.content}"
        for m in reversed(raw_messages)
        if m.content.strip()
    ]
    if not lines:
        return None

    thread_text = "\n".join(lines)
    portfolio_score, title, summary = await asyncio.to_thread(score_portfolio_worthiness, thread_text)
    activity_score, last_active_at = compute_activity_score(raw_messages)

    log.info("[tracker] Thread %d → portfolio=%.1f activity=%.2f title=%r",
             thread.id, portfolio_score, activity_score, title)

    return {
        "thread_id": thread.id,
        "title": title,
        "summary": summary,
        "portfolio_score": portfolio_score,
        "activity_score": activity_score,
        "last_active_at": last_active_at,
    }


async def run_tracker(client: discord.Client, bot_threads: set[int]) -> None:
    """Score all tracked threads and persist results. Also poll GitHub for building ideas."""
    log.info("[tracker] Starting scoring run — %d threads, polling GitHub for building ideas.", len(bot_threads))
    scored = 0

    # -- Score ideating threads via Discord activity --
    for thread_id in list(bot_threads):
        thread: Optional[discord.Thread] = client.get_channel(thread_id)  # type: ignore[assignment]
        if thread is None:
            try:
                thread = await client.fetch_channel(thread_id)  # type: ignore[assignment]
            except Exception:
                log.warning("[tracker] Could not fetch thread %d, skipping.", thread_id)
                continue

        if not isinstance(thread, discord.Thread):
            continue

        record = await score_thread(thread)
        if record is None:
            continue

        try:
            await store.upsert_idea(**record)
            scored += 1
        except Exception:
            log.exception("[tracker] Failed to upsert idea for thread %d", thread_id)

    # -- Poll GitHub commit activity for building ideas --
    building = await store.load_building_ideas()
    if building:
        log.info("[tracker] Polling GitHub activity for %d building idea(s).", len(building))
        from tools.github import fetch_github_activity
        for idea in building:
            repo_url = idea.get("repo_url", "")
            if not repo_url:
                continue
            try:
                commit_count = await asyncio.to_thread(fetch_github_activity, repo_url)
                # Map commit count (0+) to an activity score (1–10)
                # 0 commits → 1.0, 10+ commits → 10.0
                activity_score = round(min(10.0, 1.0 + commit_count * 0.9), 2)
                await store.update_idea_activity(idea["thread_id"], activity_score)
                log.info(
                    "[tracker] %r — %d commits this week → activity_score=%.2f",
                    idea.get("title"), commit_count, activity_score,
                )
            except Exception:
                log.exception("[tracker] GitHub poll failed for %s", repo_url)

    log.info("[tracker] Scored %d ideating threads, %d building ideas polled.", scored, len(building))
