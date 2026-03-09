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

    top_idea, all_ideas, cal_events = await asyncio.gather(
        store.get_top_idea(),
        store.load_ideas(),
        asyncio.to_thread(calendar_client.get_todays_events),
    )

    lines: list[str] = []
    lines.append(f"🌅 **Good morning! Here's your daily brief — {date_str}**")
    lines.append("")

    lines.append("🏆 **Top Project Idea**")
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

    lines.append("📅 **Today's Calendar**")
    if cal_events:
        for event in cal_events:
            lines.append(f"• {event['start_time']} — {event['summary']}")
    else:
        lines.append("• Nothing on the calendar today.")
    lines.append("")

    lines.append("💡 **Idea Pipeline**")
    if all_ideas:
        count = len(all_ideas)
        runners_up = all_ideas[1:4] if len(all_ideas) > 1 else []
        if runners_up:
            next_titles = ", ".join(
                f"{i['title']} ({i['combined_score']:.1f})" for i in runners_up
            )
            lines.append(f"{count} idea{'s' if count != 1 else ''} tracked.")
            lines.append(f"Also consider: {next_titles}")
        else:
            lines.append(f"{count} idea tracked.")
    else:
        lines.append("No ideas tracked yet.")

    log.info("[brief] Morning brief assembled.")
    return "\n".join(lines)
