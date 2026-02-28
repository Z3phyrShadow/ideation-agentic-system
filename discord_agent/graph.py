"""
graph.py
--------
Defines the LangGraph agent graph for the Discord AI assistant.

Architecture (ReAct loop):

    START → agent → should_use_tools? ─yes─→ tools → agent → …
                                      └─no──→ END

The agent node calls the LLM (with tools bound). If the model requests one
or more tool calls, the ToolNode executes them and loops back. When the model
produces a plain text response (no tool calls), the graph terminates.
"""

import logging
from typing import Annotated, Literal

from langchain_core.messages import BaseMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from discord_agent.llm import get_llm_with_tools
from discord_agent.tools import ALL_TOOLS

log = logging.getLogger("discord_agent.graph")


class AgentState(TypedDict):
    """
    The state schema for the LangGraph agent.

    messages: A list of LangChain BaseMessage objects representing the
              full conversation history, including the system prompt.
              The add_messages reducer handles merging new messages.
    """

    messages: Annotated[list[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def agent_node(state: AgentState) -> dict:
    """
    Core agent node: sends the current message history to the tool-bound LLM
    and returns its response (either a plain AIMessage or one with tool calls).
    """
    llm = get_llm_with_tools()
    response: AIMessage = llm.invoke(state["messages"])
    log.info(
        "[agent_node] tool_calls=%d",
        len(response.tool_calls) if hasattr(response, "tool_calls") else 0,
    )
    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _should_use_tools(state: AgentState) -> Literal["tools", "__end__"]:
    """
    Conditional edge: route to the ToolNode if the last AIMessage contains
    tool calls, otherwise end the graph.
    """
    last: BaseMessage = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    """Compile and return the LangGraph StateGraph with a ReAct tool loop."""
    builder = StateGraph(AgentState)

    # Nodes
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(ALL_TOOLS))

    # Edges
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _should_use_tools, ["tools", END])
    builder.add_edge("tools", "agent")  # loop back after tool execution

    return builder.compile()


# Compiled graph — module-level singleton, built once at import time.
_graph = _build_graph()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def _extract_text(content) -> str:
    """
    Safely extract a plain string from an LLM message's content field.

    Gemini (and other multimodal models) sometimes return content as a list
    of typed blocks, e.g.:
        [{'type': 'text', 'text': '...', 'extras': {...}}]
    instead of a plain string. This helper handles both cases.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content)


def run_graph(messages: list[BaseMessage]) -> str:
    """
    Run the agent graph with the given message history.

    Args:
        messages: Full conversation history including the system prompt.

    Returns:
        The text content of the last AI message produced by the graph.
    """
    result = _graph.invoke({"messages": messages})
    # Walk backwards to find the last AIMessage (ToolMessage at end is unexpected
    # but guarded against).
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage):
            return _extract_text(msg.content)
    return _extract_text(result["messages"][-1].content)
