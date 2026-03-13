"""
brief.py (shared)
-----------------
Daily morning brief assembler.
"""

import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger("shared.brief")


async def build_brief() -> str:
    from shared import store
    from shared import calendar_client

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %d %B %Y")

    top_idea, building_ideas, all_ideas, cal_events = await asyncio.gather(
        store.get_top_idea(),
        store.load_building_ideas(),
        store.load_ideas(),
        asyncio.to_thread(calendar_client.get_todays_events),
    )

    lines: list[str] = []
    lines.append(f"🌅 **Good morning! Here's your daily brief — {date_str}**")
    lines.append("")

    # -- Currently Building section --
    if building_ideas:
        lines.append("🔨 **Currently Building**")
        for idea in building_ideas:
            title = idea.get("title") or "Untitled"
            activity = idea.get("activity_score", 0)
            repo = idea.get("repo_url", "")
            # Reverse-map activity score to approximate commit count for display
            commits = max(0, round((activity - 1.0) / 0.9))
            commit_str = f"{commits} commit{'s' if commits != 1 else ''} this week"
            repo_str = f" — {repo}" if repo else ""
            lines.append(f"• **{title}** ({commit_str}){repo_str}")
        lines.append("")

    # -- Top Unstarted Idea section --
    lines.append("💡 **Top Unstarted Idea**")
    if top_idea:
        score = top_idea.get("combined_score", 0)
        title = top_idea.get("title") or "Untitled"
        summary = top_idea.get("summary", "").strip()
        lines.append(f"**{title}** (score: {score:.1f})")
        if summary:
            lines.append(summary)
    else:
        lines.append("No ideas scored yet — drop something in #ideas!")
    lines.append("")

    # -- Calendar --
    lines.append("📅 **Today's Calendar**")
    if cal_events:
        for event in cal_events:
            lines.append(f"• {event['start_time']} — {event['summary']}")
    else:
        lines.append("• Nothing on the calendar today.")
    lines.append("")

    # -- Pipeline stats --
    ideating = [i for i in all_ideas if i.get("status", "ideating") == "ideating"]
    total = len(all_ideas)
    building_count = len(building_ideas)
    lines.append("📊 **Pipeline**")
    lines.append(
        f"{total} idea{'s' if total != 1 else ''} total — "
        f"{building_count} building, {len(ideating)} in ideation."
    )

    log.info("[brief] Morning brief assembled.")
    return "\n".join(lines)
