"""
memory.py
---------
Reconstructs LangGraph-compatible message history from a Discord thread.

Strategy:
  - Fetch the last N messages from the thread (chronological order).
  - Prepend a SystemMessage with the agent's personality prompt.
  - Map Discord messages to LangChain message types:
      Bot messages with HUMAN_PREFIX  -> HumanMessage  (user's words, relayed by bot)
      Bot messages without prefix     -> AIMessage
      User messages                   -> HumanMessage
  - Skip sentinel messages (e.g. "Agent initialized.") that are not
    part of the real conversation.

The HUMAN_PREFIX convention solves a role ordering constraint imposed by
Gemini: the Gemini API requires requests to end with a user (human) role
message. When the bot relays a user's opening /chat message into the thread,
it prefixes it with HUMAN_PREFIX so this module can correctly classify it as
a HumanMessage rather than an AIMessage, keeping the message sequence valid.
"""

import discord
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

# Marker prefix used when the bot posts a human's message on their behalf.
# memory.py detects this prefix and maps the message to HumanMessage.
HUMAN_PREFIX: str = "👤 "

# Messages with this exact content are bot control messages, not conversation.
_SKIP_CONTENT: frozenset[str] = frozenset({"Agent initialized."})

# Maximum number of Discord messages to fetch per invocation.
_HISTORY_LIMIT: int = 30


async def build_message_history(
    thread: discord.Thread,
    system_prompt: str,
    bot_id: int,
) -> list[BaseMessage]:
    """
    Build a LangChain message list from a Discord thread's history.

    Args:
        thread:        The Discord Thread object to fetch history from.
        system_prompt: The agent's system/personality prompt text.
        bot_id:        The Discord user ID of the bot, used to identify
                       which messages are AI responses.

    Returns:
        A list of BaseMessage objects ordered oldest-to-newest, with a
        SystemMessage prepended. Ready to pass directly to LangGraph.
    """
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]

    # thread.history() yields messages newest-first; we reverse for chronology.
    raw_messages: list[discord.Message] = []
    async for msg in thread.history(limit=_HISTORY_LIMIT):
        raw_messages.append(msg)

    # Reverse to get chronological (oldest first) order.
    raw_messages.reverse()

    for msg in raw_messages:
        # Skip empty messages and bot control messages.
        content = msg.content.strip()
        if not content or content in _SKIP_CONTENT:
            continue

        if msg.author.id == bot_id:
            # Bot relayed a human's message (prefixed with HUMAN_PREFIX).
            if content.startswith(HUMAN_PREFIX):
                messages.append(HumanMessage(content=content[len(HUMAN_PREFIX):]))
            else:
                messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))

    return messages
