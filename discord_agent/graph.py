"""
graph.py
--------
Defines the LangGraph agent graph for the Discord AI assistant.

Architecture:
    START -> agent_node -> END

The graph is designed for easy extensibility — additional nodes (e.g. a
planner, evaluator, or tool executor) can be inserted between START and END
without changing the external interface.
"""

from typing import Annotated

from langchain_core.messages import BaseMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from discord_agent.llm import get_llm


class AgentState(TypedDict):
    """
    The state schema for the LangGraph agent.

    messages: A list of LangChain BaseMessage objects representing the
              full conversation history, including the system prompt.
              The add_messages reducer handles merging new messages.
    """

    messages: Annotated[list[BaseMessage], add_messages]


def agent_node(state: AgentState) -> dict:
    """
    Core agent node: sends the current message history to the LLM and
    returns the model's response as a new AIMessage.

    Args:
        state: The current AgentState containing the message history.

    Returns:
        A dict with 'messages' containing the LLM's response, which
        LangGraph will merge into the state via the add_messages reducer.
    """
    llm = get_llm()
    response: AIMessage = llm.invoke(state["messages"])
    return {"messages": [response]}


def _build_graph() -> StateGraph:
    """Compile and return the LangGraph StateGraph."""
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile()


# Compiled graph — module-level singleton, built once at import time.
_graph = _build_graph()


def run_graph(messages: list[BaseMessage]) -> str:
    """
    Run the agent graph with the given message history.

    Args:
        messages: Full conversation history including the system prompt.

    Returns:
        The text content of the last AI message produced by the graph.
    """
    result = _graph.invoke({"messages": messages})
    last_message: BaseMessage = result["messages"][-1]
    return str(last_message.content)
