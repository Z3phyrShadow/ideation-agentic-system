"""
main.py
-------
Entry point for the Discord AI assistant bot.

Responsibilities:
  - Configure Discord client with correct intents.
  - Listen for messages posted in the #ideas channel and automatically create
    a thread on each message, then reply with the agent's first response.
  - Auto-respond to follow-up messages inside bot-created threads.
  - Load thread IDs from SQLite on startup (persistence across restarts).
  - Load the system prompt from system_prompt.txt once at startup.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import discord

from discord_agent import store, tracker
from discord_agent.config import AGENT_CHANNEL_ID, DISCORD_TOKEN
from discord_agent.graph import run_graph
from discord_agent.memory import build_message_history

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("discord_agent")

# ---------------------------------------------------------------------------
# System prompt — loaded once at startup
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"

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
    """
    Build a small text block listing attachment URLs so the agent can
    call read_attachment() on them.

    Returns an empty string if there are no attachments.
    """
    if not attachments:
        return ""
    lines = ["\n[Attachments in this message:]"] + [
        f"  • {a.filename}: {a.url}" for a in attachments
    ]
    return "\n".join(lines)


async def _get_agent_response(thread: discord.Thread, seed_content: str | None = None) -> str:
    """
    Reconstruct message history from the thread, invoke the LangGraph agent,
    and return the response string.

    Args:
        thread:       The Discord thread to read history from.
        seed_content: Optional opening human message to inject at the start of
                      history (used when the post lives in the parent channel,
                      not inside the thread itself).

    LangGraph's .invoke() is synchronous, so we offload it to a thread pool
    to avoid blocking the Discord async event loop.
    """
    now = datetime.now(timezone.utc).strftime("%A, %d %B %Y %H:%M UTC")
    effective_prompt = f"Current date and time: {now}\n\n{SYSTEM_PROMPT}"
    messages = await build_message_history(
        thread=thread,
        system_prompt=effective_prompt,
        bot_id=client.user.id,  # type: ignore[union-attr]
        seed_content=seed_content,
    )
    response: str = await asyncio.to_thread(run_graph, messages)
    return response


_DISCORD_MAX_LEN: int = 2000


async def send_chunked(channel: discord.abc.Messageable, content: str) -> None:
    """
    Send a message to a Discord channel, splitting it into chunks if it
    exceeds Discord's 2000-character message limit.

    Splits are made at the last whitespace boundary within the limit to
    avoid breaking words mid-token.

    Args:
        channel: Any Discord messageable (Thread, TextChannel, etc.).
        content: The full text to send.
    """
    if not content:
        return
    while content:
        if len(content) <= _DISCORD_MAX_LEN:
            await channel.send(content)
            break
        # Find the last whitespace within the limit to split cleanly.
        split_at = content.rfind(" ", 0, _DISCORD_MAX_LEN)
        if split_at == -1:
            # No whitespace found; hard-cut at the limit.
            split_at = _DISCORD_MAX_LEN
        await channel.send(content[:split_at])
        content = content[split_at:].lstrip()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Background tracker loop
# ---------------------------------------------------------------------------

_TRACKER_INTERVAL_SECONDS: int = 60 * 60  # 1 hour


async def _tracker_loop() -> None:
    """
    Background task: runs the tracker once immediately on startup, then
    repeats every hour. Errors are caught so the loop never silently dies.
    """
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            await tracker.run_tracker(client, bot_threads)
        except Exception:
            log.exception("[tracker_loop] Unhandled error during tracker run")
        await asyncio.sleep(_TRACKER_INTERVAL_SECONDS)


@client.event
async def on_ready() -> None:
    """Initialise DB, restore persisted thread IDs, and start background tracker."""
    log.info("Logged in as %s (ID: %s)", client.user, client.user.id)  # type: ignore[union-attr]

    # Initialise SQLite and load previously created thread IDs.
    await store.init_db()
    loaded = await store.load_threads()
    bot_threads.update(loaded)
    log.info("Loaded %d persisted thread IDs from database.", len(loaded))
    log.info("Bot is ready. Listening on channel ID %d for new ideas.", AGENT_CHANNEL_ID)

    # Launch the hourly tracker in the background.
    asyncio.create_task(_tracker_loop())
    log.info("Background tracker loop started (interval=%ds).", _TRACKER_INTERVAL_SECONDS)


@client.event
async def on_message(message: discord.Message) -> None:
    """
    Handle two cases:

    1. A user posts a NEW message directly in the #ideas channel
       → Create a thread on that message and get the agent's opening response.

    2. A user sends a FOLLOW-UP inside one of the bot-created threads
       → Auto-respond with the agent.
    """
    # Ignore all bot messages (including our own).
    if message.author.bot:
        return

    # ------------------------------------------------------------------
    # Case 1 — New message in the #ideas channel → spin up a thread
    # ------------------------------------------------------------------
    if (
        isinstance(message.channel, discord.TextChannel)
        and message.channel.id == AGENT_CHANNEL_ID
    ):
        channel: discord.TextChannel = message.channel

        # Build a short thread name from the message content.
        snippet = message.content[:80].strip().replace("\n", " ")
        thread_name = f"💡 {snippet}" if snippet else f"idea-{message.id}"
        thread_name = thread_name[:100]  # Discord limit

        log.info(
            "New idea in #ideas from %s (msg %d): %s",
            message.author,
            message.id,
            message.content[:80],
        )

        # Create a public thread anchored to this specific message.
        thread: discord.Thread = await channel.create_thread(
            name=thread_name,
            message=message,
            type=discord.ChannelType.public_thread,
        )

        # Track the thread in memory and persist to DB.
        bot_threads.add(thread.id)
        await store.save_thread(thread.id)
        log.info("Created thread %d ('%s')", thread.id, thread_name)

        # Build seed content: the original message text + any attachment URLs
        # so the agent can decide to call read_attachment() on them.
        seed = message.content + _attachment_note(message.attachments)

        async with thread.typing():
            try:
                response = await _get_agent_response(thread, seed_content=seed)
            except Exception:
                log.exception("Error generating opening response for thread %d", thread.id)
                await thread.send(
                    "⚠️ An error occurred while generating a response. Please try again."
                )
                return

        await send_chunked(thread, response)

        # Score the new thread immediately in the background (non-blocking).
        asyncio.create_task(_score_and_persist_thread(thread))
        return

    # ------------------------------------------------------------------
    # Case 2 — Follow-up message inside a bot-created thread
    # ------------------------------------------------------------------
    if not isinstance(message.channel, discord.Thread):
        return

    thread = message.channel

    # Only respond in threads we created.
    if thread.id not in bot_threads:
        return

    log.info(
        "Follow-up in thread %d from %s: %s",
        thread.id,
        message.author,
        message.content[:80],
    )

    async with thread.typing():
        try:
            response = await _get_agent_response(thread)
        except Exception:
            log.exception("Error generating response for thread %d", thread.id)
            await thread.send(
                "⚠️ An error occurred while generating a response. Please try again."
            )
            return

    await send_chunked(thread, response)


# ---------------------------------------------------------------------------
# Single-thread fast scorer (called on new thread creation)
# ---------------------------------------------------------------------------


async def _score_and_persist_thread(thread: discord.Thread) -> None:
    """
    Score a single newly-created thread and update the DB + Calendar.
    Runs as a fire-and-forget asyncio task.
    """
    try:
        record = await tracker.score_thread(thread)
        if record is None:
            return
        await store.upsert_idea(**record)
        top = await store.get_top_idea()
        if top:
            tracker._trigger_calendar_update(top)
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
