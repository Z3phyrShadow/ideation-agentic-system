"""
main.py
-------
Entry point for the Discord AI assistant bot.

Responsibilities:
  - Configure Discord client with correct intents.
  - Register the /chat slash command to create a new thread and send the
    first agent response.
  - Listen for messages in bot-created threads and auto-respond.
  - Load thread IDs from SQLite on startup (persistence across restarts).
  - Load the system prompt from system_prompt.txt once at startup.
"""

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands

from discord_agent import store
from discord_agent.config import AGENT_CHANNEL_ID, DISCORD_TOKEN
from discord_agent.graph import run_graph
from discord_agent.memory import HUMAN_PREFIX, build_message_history

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
tree = app_commands.CommandTree(client)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _get_agent_response(thread: discord.Thread) -> str:
    """
    Reconstruct message history from the thread, invoke the LangGraph agent,
    and return the response string.

    LangGraph's .invoke() is synchronous, so we offload it to a thread pool
    to avoid blocking the Discord async event loop.
    """
    messages = await build_message_history(
        thread=thread,
        system_prompt=SYSTEM_PROMPT,
        bot_id=client.user.id,  # type: ignore[union-attr]
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


@client.event
async def on_ready() -> None:
    """Sync slash commands, initialise DB, and restore persisted thread IDs."""
    log.info("Logged in as %s (ID: %s)", client.user, client.user.id)  # type: ignore[union-attr]

    # Initialise SQLite and load previously created thread IDs.
    await store.init_db()
    loaded = await store.load_threads()
    bot_threads.update(loaded)
    log.info("Loaded %d persisted thread IDs from database.", len(loaded))

    # Sync slash commands to all guilds.
    await tree.sync()
    log.info("Slash commands synced. Bot is ready.")


@client.event
async def on_message(message: discord.Message) -> None:
    """
    Auto-respond to user messages posted in bot-created threads.

    Conditions for responding:
      1. The message is not from a bot (prevents self-loops).
      2. The message is in a thread (not a top-level channel).
      3. The thread ID is in bot_threads (only our threads).
    """
    # Ignore all bot messages (including our own).
    if message.author.bot:
        return

    # Only react to messages inside threads.
    if not isinstance(message.channel, discord.Thread):
        return

    thread: discord.Thread = message.channel

    # Only respond in threads we created.
    if thread.id not in bot_threads:
        return

    log.info(
        "New message in bot thread %d from %s: %s",
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
# Slash command: /chat
# ---------------------------------------------------------------------------


@tree.command(name="chat", description="Start a new AI agent conversation thread.")
@app_commands.describe(message="Your opening message to the agent.")
async def chat_command(interaction: discord.Interaction, message: str) -> None:
    """
    /chat <message>

    Creates a new thread in the designated agent channel, posts the user's
    opening message through LangGraph, and replies with the agent's response.
    """
    await interaction.response.defer(ephemeral=True)

    # Fetch the designated channel where threads are created.
    channel = client.get_channel(AGENT_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send(
            "⚠️ Agent channel not found or is not a text channel. "
            "Check AGENT_CHANNEL_ID in your .env file.",
            ephemeral=True,
        )
        return

    # Create a new public thread in the agent channel.
    thread_name = f"chat-{interaction.user.name}-{interaction.id}"[:100]
    thread = await channel.create_thread(
        name=thread_name,
        type=discord.ChannelType.public_thread,
    )

    # Track the thread in memory and persist to DB.
    bot_threads.add(thread.id)
    await store.save_thread(thread.id)
    log.info("Created thread %d ('%s') for user %s", thread.id, thread_name, interaction.user)

    # Post the initialisation sentinel message.
    await thread.send("Agent initialized.")

    # Send the user's opening message to the thread, prefixed with HUMAN_PREFIX
    # so memory.py correctly maps it as a HumanMessage (not AIMessage).
    # This satisfies Gemini's constraint that requests end with a user role.
    await thread.send(f"{HUMAN_PREFIX}{message}")

    async with thread.typing():
        try:
            response = await _get_agent_response(thread)
        except Exception:
            log.exception("Error on initial /chat response for thread %d", thread.id)
            await thread.send("⚠️ Failed to get a response from the agent. Please try again.")
            await interaction.followup.send(
                f"Thread created: {thread.mention}, but the agent failed to respond.",
                ephemeral=True,
            )
            return

    await send_chunked(thread, response)
    await interaction.followup.send(
        f"Thread created: {thread.mention}", ephemeral=True
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the Discord bot."""
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
