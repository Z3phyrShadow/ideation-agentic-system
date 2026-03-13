"""
main.py (discord_bot)
---------------------
Entry point for the Discord AI assistant bot.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import discord

from agents.tracker import scorer as tracker
from discord_bot.config import AGENT_CHANNEL_ID, CAREER_CHANNEL_ID, DISCORD_TOKEN
from agents.ideation.graph import run_graph
from agents.ideation.memory import build_message_history
from shared import store

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# System prompt — loaded once at startup
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "agents" / "ideation" / "system_prompt.txt"

try:
    SYSTEM_PROMPT: str = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
except FileNotFoundError:
    raise RuntimeError(
        f"system_prompt.txt not found at {_SYSTEM_PROMPT_PATH}. "
        "Please create the file before starting the bot."
    )

# ---------------------------------------------------------------------------
# In-memory set of bot-owned thread IDs (populated from DB on ready)
# ---------------------------------------------------------------------------

bot_threads: set[int] = set()

# ---------------------------------------------------------------------------
# Discord client setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

client = discord.Client(intents=intents)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attachment_note(attachments: list[discord.Attachment]) -> str:
    if not attachments:
        return ""
    lines = ["\n[Attachments in this message:]"] + [
        f"  • {a.filename}: {a.url}" for a in attachments
    ]
    return "\n".join(lines)


async def _get_agent_response(thread: discord.Thread, seed_content: str | None = None) -> str:
    now = datetime.now(timezone.utc).strftime("%A, %d %B %Y %H:%M UTC")
    effective_prompt = f"Current date and time: {now}\n\n{SYSTEM_PROMPT}"
    messages = await build_message_history(
        thread=thread,
        system_prompt=effective_prompt,
        bot_id=client.user.id,  # type: ignore[union-attr]
        seed_content=seed_content,
    )
    # Inject thread_id context so queue_build_task can associate the task
    from tools.build import queue_build_task as _qbt
    _qbt._current_thread_id = thread.id  # type: ignore[attr-defined]
    response: str = await asyncio.to_thread(run_graph, messages)
    return response


_DISCORD_MAX_LEN: int = 2000


async def send_chunked(channel: discord.abc.Messageable, content: str) -> None:
    if not content:
        return
    while content:
        if len(content) <= _DISCORD_MAX_LEN:
            await channel.send(content)
            break
        split_at = content.rfind(" ", 0, _DISCORD_MAX_LEN)
        if split_at == -1:
            split_at = _DISCORD_MAX_LEN
        await channel.send(content[:split_at])
        content = content[split_at:].lstrip()


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

_TRACKER_INTERVAL_SECONDS: int = 60 * 60  # 1 hour
_BRIEF_HOUR_LOCAL: int = 9               # 9 AM local time


async def _tracker_loop() -> None:
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await tracker.run_tracker(client, bot_threads)
        except Exception:
            log.exception("[tracker_loop] Unhandled error during tracker run")
        await asyncio.sleep(_TRACKER_INTERVAL_SECONDS)


async def _morning_brief_loop() -> None:
    from shared import brief as brief_module
    from shared import calendar_client
    from datetime import date

    await client.wait_until_ready()
    last_brief_date: date | None = None

    while not client.is_closed():
        now_local = datetime.now()
        today = now_local.date()

        if now_local.hour == _BRIEF_HOUR_LOCAL and last_brief_date != today:
            last_brief_date = today
            try:
                content = await brief_module.build_brief()
                await asyncio.to_thread(calendar_client.create_morning_brief_event, content)
                log.info("[brief] Morning brief calendar event created/updated for %s.", today)
            except Exception:
                log.exception("[brief] Failed to create morning brief calendar event")

        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Build task completion poller
# ---------------------------------------------------------------------------

_BUILD_POLL_INTERVAL: int = 30  # seconds
_notified_tasks: set[int] = set()  # task IDs already announced


async def _build_task_poller() -> None:
    """
    Background task: polls the DB every 30 seconds for build tasks that
    Antigravity has marked as done. Sends the '🚀 repo is live' handoff
    message to the relevant Discord thread.

    This replaces a direct async callback since the MCP server now runs as
    a separate stdio subprocess (spawned by Antigravity), not in-process.
    """
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(_BUILD_POLL_INTERVAL)
        try:
            async with __import__('aiosqlite').connect(
                __import__('pathlib').Path('threads.db')
            ) as db:
                db.row_factory = __import__('aiosqlite').Row
                cursor = await db.execute(
                    "SELECT id, thread_id, repo_url FROM build_tasks "
                    "WHERE status = 'done' AND completed_at != '' "
                    "ORDER BY completed_at DESC LIMIT 20"
                )
                rows = await cursor.fetchall()

            for row in rows:
                task_id = row['id']
                if task_id in _notified_tasks:
                    continue
                _notified_tasks.add(task_id)
                thread_id = row['thread_id']
                repo_url = row['repo_url']
                try:
                    thread = client.get_channel(thread_id)
                    if thread is None:
                        thread = await client.fetch_channel(thread_id)
                    if isinstance(thread, discord.Thread):
                        await thread.send(
                            f"\U0001f680 **Repo is live!** Antigravity has scaffolded your project.\n"
                            f"\u2192 {repo_url}\n\n"
                            f"Go forth, padawan \u2014 your destiny awaits. \u2728"
                        )
                        log.info("[mcp] Sent build-complete message to thread %d", thread_id)
                except Exception:
                    log.exception("[mcp] Failed to notify thread %d", thread_id)
        except Exception:
            log.exception("[build_poller] Unhandled error")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@client.event
async def on_ready() -> None:
    log.info("Logged in as %s (ID: %s)", client.user, client.user.id)  # type: ignore[union-attr]

    await store.init_db()
    loaded = await store.load_threads()
    bot_threads.update(loaded)
    log.info("Loaded %d persisted thread IDs from database.", len(loaded))
    log.info("Bot is ready. Listening on channel ID %d for new ideas.", AGENT_CHANNEL_ID)

    asyncio.create_task(_tracker_loop())
    log.info("Background tracker loop started (interval=%ds).", _TRACKER_INTERVAL_SECONDS)

    asyncio.create_task(_morning_brief_loop())
    log.info("Morning brief loop started (fires at 09:00 local time).")

    asyncio.create_task(_build_task_poller())
    log.info("Build task poller started (interval=%ds).", _BUILD_POLL_INTERVAL)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    # ------------------------------------------------------------------
    # Case 0 — New message in #career channel → run career agent
    # ------------------------------------------------------------------
    if (
        isinstance(message.channel, discord.TextChannel)
        and message.channel.id == CAREER_CHANNEL_ID
    ):
        pdf_attachment = next(
            (a for a in message.attachments if a.filename.lower().endswith(".pdf")),
            None,
        )
        if pdf_attachment is None:
            await message.reply(
                "👋 Please attach your **resume PDF** along with your message. "
                "Example: *'I want to become a Machine Learning Engineer'* + resume.pdf"
            )
            return

        snippet = message.content[:60].strip().replace("\n", " ") or "Career Setup"
        thread_name = f"🎯 {snippet}"[:100]
        career_thread: discord.Thread = await message.channel.create_thread(
            name=thread_name,
            message=message,
            type=discord.ChannelType.public_thread,
        )
        asyncio.create_task(run_career_setup(message, career_thread, pdf_attachment))
        return

    # ------------------------------------------------------------------
    # Case 1 — New message in the #ideas channel → spin up a thread
    # ------------------------------------------------------------------
    if (
        isinstance(message.channel, discord.TextChannel)
        and message.channel.id == AGENT_CHANNEL_ID
    ):
        channel: discord.TextChannel = message.channel

        snippet = message.content[:80].strip().replace("\n", " ")
        thread_name = f"💡 {snippet}" if snippet else f"idea-{message.id}"
        thread_name = thread_name[:100]

        log.info(
            "New idea in #ideas from %s (msg %d): %s",
            message.author, message.id, message.content[:80],
        )

        thread: discord.Thread = await channel.create_thread(
            name=thread_name,
            message=message,
            type=discord.ChannelType.public_thread,
        )

        bot_threads.add(thread.id)
        await store.save_thread(thread.id)
        log.info("Created thread %d ('%s')", thread.id, thread_name)

        seed = message.content + _attachment_note(message.attachments)

        async with thread.typing():
            try:
                response = await _get_agent_response(thread, seed_content=seed)
            except Exception:
                log.exception("Error generating opening response for thread %d", thread.id)
                await thread.send("⚠️ An error occurred while generating a response. Please try again.")
                return

        await send_chunked(thread, response)
        asyncio.create_task(_score_and_persist_thread(thread))
        return

    # ------------------------------------------------------------------
    # Case 2 — Follow-up inside a bot-created thread
    # ------------------------------------------------------------------
    if not isinstance(message.channel, discord.Thread):
        return

    thread = message.channel
    if thread.id not in bot_threads:
        return

    log.info("Follow-up in thread %d from %s: %s", thread.id, message.author, message.content[:80])

    async with thread.typing():
        try:
            response = await _get_agent_response(thread)
        except Exception:
            log.exception("Error generating response for thread %d", thread.id)
            await thread.send("⚠️ An error occurred while generating a response. Please try again.")
            return

    await send_chunked(thread, response)


# ---------------------------------------------------------------------------
# Career setup handler
# ---------------------------------------------------------------------------


async def run_career_setup(
    message: discord.Message,
    thread: discord.Thread,
    pdf_attachment: discord.Attachment,
) -> None:
    """
    Orchestrate the career agent flow for a #career message.

    1. Extract text from PDF (pypdf, with Gemini Vision OCR fallback).
    2. Extract target role from message.
    3. Run the career ReAct agent → report + project suggestions.
    4. Post report, seed project ideas into #ideas, persist profile.
    """
    from agents.career.graph import run_career_agent
    from shared.llm import get_llm
    import json

    log.info("[career] Starting career setup for %s (thread %d)", message.author, thread.id)

    # -- Step 1: Download and extract PDF (with OCR fallback) --
    await thread.send("📄 Reading your resume…")
    try:
        async with thread.typing():
            from tools.ocr import extract_text_with_ocr_fallback
            pdf_bytes = await pdf_attachment.read()
            resume_text = await asyncio.to_thread(
                extract_text_with_ocr_fallback, pdf_bytes, "application/pdf"
            )
        if not resume_text or len(resume_text.strip()) < 50:
            await thread.send(
                "⚠️ Couldn't extract text from the PDF. "
                "Try a non-scanned PDF or paste your resume as text."
            )
            return
    except Exception:
        log.exception("[career] PDF extraction failed for thread %d", thread.id)
        await thread.send("⚠️ Failed to read the PDF. Please try again.")
        return

    # -- Step 2: Extract target role from message --
    from langchain_core.messages import HumanMessage, SystemMessage
    llm = get_llm()
    role_result = await asyncio.to_thread(
        llm.invoke,
        [
            SystemMessage(content=(
                "Extract the target job role from the user's message. "
                "Return ONLY the job title (e.g. 'ML Engineer'). "
                "If none found, return: NONE"
            )),
            HumanMessage(content=message.content),
        ]
    )
    role = str(role_result.content).strip().strip('"').strip("'")
    if role.upper() == "NONE" or not role:
        await thread.send(
            "❓ I couldn't detect a target role in your message. "
            "What role are you aiming for? (e.g. 'Machine Learning Engineer')"
        )
        def check(m: discord.Message) -> bool:
            return m.channel.id == thread.id and not m.author.bot
        try:
            reply = await client.wait_for("message", check=check, timeout=300)
            role = reply.content.strip()
        except asyncio.TimeoutError:
            await thread.send("⏰ Timed out. Run the setup again.")
            return

    await thread.send(f"🎯 Target role: **{role}**\n🤖 Analysing your profile and researching the market…")

    # -- Step 3: Run the career ReAct agent --
    async with thread.typing():
        report, project_ideas = await run_career_agent(resume_text, role)

    # -- Step 4: Post report --
    await send_chunked(thread, report)

    # -- Step 5: Seed project ideas into #ideas --
    if project_ideas:
        await thread.send(
            f"💡 I've seeded **{len(project_ideas)} project idea(s)** "
            f"into <#{AGENT_CHANNEL_ID}> based on your skill gaps!"
        )
        ideas_channel = client.get_channel(AGENT_CHANNEL_ID)
        if isinstance(ideas_channel, discord.TextChannel):
            for idea in project_ideas:
                await ideas_channel.send(
                    f"💡 *(Career suggestion for {message.author.mention})*\n{idea}"
                )

    # -- Step 6: Persist career profile --
    try:
        await store.upsert_career_profile(
            user_id=message.author.id,
            role_target=role,
            skills_json=json.dumps([]),  # profile extraction now inside agent
        )
        log.info("[career] Profile saved for user %d", message.author.id)
    except Exception:
        log.exception("[career] Failed to save career profile")

    await thread.send(
        "✅ **Setup complete!** Your profile is saved. "
        "Drop a new message in this thread anytime to refine your plan."
    )


# ---------------------------------------------------------------------------
# Single-thread fast scorer
# ---------------------------------------------------------------------------


async def _score_and_persist_thread(thread: discord.Thread) -> None:
    try:
        record = await tracker.score_thread(thread)
        if record is None:
            return
        await store.upsert_idea(**record)
    except Exception:
        log.exception("[_score_and_persist_thread] Failed for thread %d", thread.id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Discord bot."""
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
