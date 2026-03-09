"""
memory.py (ideation agent)
--------------------------
Reconstructs LangGraph-compatible message history from a Discord thread.
"""

import discord
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

HUMAN_PREFIX: str = "👤 "
_SKIP_CONTENT: frozenset[str] = frozenset({"Agent initialized."})
_HISTORY_LIMIT: int = 30


async def build_message_history(
    thread: discord.Thread,
    system_prompt: str,
    bot_id: int,
    seed_content: str | None = None,
) -> list[BaseMessage]:
    """Build a LangChain message list from a Discord thread's history."""
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]

    if seed_content:
        messages.append(HumanMessage(content=seed_content))

    raw_messages: list[discord.Message] = []
    async for msg in thread.history(limit=_HISTORY_LIMIT):
        raw_messages.append(msg)

    raw_messages.reverse()

    for msg in raw_messages:
        content = msg.content.strip()
        if not content or content in _SKIP_CONTENT:
            continue

        if msg.author.id == bot_id:
            if content.startswith(HUMAN_PREFIX):
                messages.append(HumanMessage(content=content[len(HUMAN_PREFIX):]))
            else:
                messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))

    return messages
