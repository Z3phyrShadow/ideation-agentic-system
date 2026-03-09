"""
graph.py (ideation agent)
-------------------------
LangGraph ReAct agent for the ideation Discord assistant.

Architecture:
    START → agent → should_use_tools? ─yes─→ tools → agent → …
                                       └─no──→ END
"""

import logging
from typing import Annotated, Literal

from langchain_core.messages import BaseMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from shared.llm import get_llm_with_tools
from tools import ALL_TOOLS

log = logging.getLogger("agents.ideation.graph")


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def agent_node(state: AgentState) -> dict:
    llm = get_llm_with_tools()
    response: AIMessage = llm.invoke(state["messages"])
    log.info(
        "[agent_node] tool_calls=%d",
        len(response.tool_calls) if hasattr(response, "tool_calls") else 0,
    )
    return {"messages": [response]}


def _should_use_tools(state: AgentState) -> Literal["tools", "__end__"]:
    last: BaseMessage = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return END


def _build_graph() -> StateGraph:
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(ALL_TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _should_use_tools, ["tools", END])
    builder.add_edge("tools", "agent")
    return builder.compile()


_graph = _build_graph()


def _extract_text(content) -> str:
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
    """Run the ideation agent graph and return the final AI response text."""
    result = _graph.invoke({"messages": messages})
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage):
            return _extract_text(msg.content)
    return _extract_text(result["messages"][-1].content)
