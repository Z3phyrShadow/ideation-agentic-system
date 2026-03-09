"""
graph.py (career agent)
-----------------------
LangGraph ReAct agent for career coaching and skill gap analysis.

Architecture:
    START → career_agent → should_continue?
                              yes → tools → career_agent (loop)
                              no  → END

The agent receives the resume text and target role, then autonomously
decides which Tavily market searches to run based on the resume content.
It produces the full skill gap report as its final message, including
a structured project suggestions block for the bot to parse and seed
into #ideas.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from agents.career.prompts import CAREER_SYSTEM_PROMPT
from shared.llm import get_llm

log = logging.getLogger("agents.career.graph")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class CareerState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    role: str
    resume_text: str


# ---------------------------------------------------------------------------
# Tools available to the career agent
# ---------------------------------------------------------------------------


@tool
def search_market(query: str) -> str:
    """
    Search the web for current job market information.

    Use this to research skills, certifications, tools, and trends
    relevant to the candidate's target role. Be specific in your query
    (e.g. "best ML Engineer certifications 2026" not just "ML certs").

    Args:
        query: A specific search query about skills, certs, or market trends.

    Returns:
        Top search results with titles, URLs, and snippets.
    """
    log.info("[career_agent] search_market: %r", query)
    try:
        from tavily import TavilyClient
        from discord_bot.config import TAVILY_API_KEY

        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(query=query, max_results=4, search_depth="basic")
        results = response.get("results", [])
        if not results:
            return "[No results found.]"
        lines = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("content", "").strip()[:400]
            lines.append(f"**{title}**\nURL: {url}\n{snippet}")
        return "\n\n---\n\n".join(lines)
    except Exception as exc:
        log.exception("[career_agent] search_market failed")
        return f"[Search failed: {exc}]"


_CAREER_TOOLS = [search_market]


# ---------------------------------------------------------------------------
# LLM with tools bound (career-specific)
# ---------------------------------------------------------------------------


def _get_career_llm():
    return get_llm().bind_tools(_CAREER_TOOLS)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%A, %d %B %Y %H:%M UTC")


def career_agent_node(state: CareerState) -> dict:
    llm = _get_career_llm()
    response = llm.invoke(state["messages"])
    log.info(
        "[career_agent] tool_calls=%d",
        len(response.tool_calls) if hasattr(response, "tool_calls") else 0,
    )
    return {"messages": [response]}


def _should_continue(state: CareerState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def _build_career_graph():
    builder = StateGraph(CareerState)
    builder.add_node("career_agent", career_agent_node)
    builder.add_node("tools", ToolNode(_CAREER_TOOLS))
    builder.add_edge(START, "career_agent")
    builder.add_conditional_edges("career_agent", _should_continue, ["tools", END])
    builder.add_edge("tools", "career_agent")
    return builder.compile()


_career_graph = _build_career_graph()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) and block.get("type") == "text"
            else block if isinstance(block, str) else ""
            for block in content
        ]
        return "\n".join(p for p in parts if p).strip()
    return str(content)


def parse_project_suggestions(report: str) -> list[str]:
    """
    Extract the project suggestions JSON block from the agent's report.

    The agent is instructed to end its report with a block like:
        <!-- PROJECTS: ["idea1", "idea2"] -->
    """
    import json
    match = re.search(r"<!--\s*PROJECTS:\s*(\[.*?\])\s*-->", report, re.DOTALL)
    if not match:
        return []
    try:
        ideas = json.loads(match.group(1))
        return [str(i).strip() for i in ideas if i]
    except Exception:
        log.warning("[career_agent] Failed to parse project suggestions block")
        return []


def clean_report(report: str) -> str:
    """Remove the hidden PROJECTS block before posting to Discord."""
    return re.sub(r"<!--\s*PROJECTS:.*?-->", "", report, flags=re.DOTALL).strip()


async def run_career_agent(resume_text: str, role: str) -> tuple[str, list[str]]:
    """
    Run the ReAct career agent.

    Args:
        resume_text: Plain text content of the candidate's resume.
        role:        Target job role (e.g. "ML Engineer").

    Returns:
        (report, project_ideas) where:
            report        — Discord-formatted skill gap report
            project_ideas — list of project idea strings for #ideas
    """
    now = _now_str()
    system_content = f"Current date and time: {now}\n\n{CAREER_SYSTEM_PROMPT}"

    initial_human = (
        f"**Resume:**\n{resume_text[:10_000]}\n\n"
        f"**Target Role:** {role}"
    )

    initial_state: CareerState = {
        "messages": [
            SystemMessage(content=system_content),
            HumanMessage(content=initial_human),
        ],
        "role": role,
        "resume_text": resume_text,
    }

    result = await asyncio.to_thread(_career_graph.invoke, initial_state)

    raw_report = ""
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage):
            raw_report = _extract_text(msg.content)
            break

    project_ideas = parse_project_suggestions(raw_report)
    report = clean_report(raw_report)

    log.info(
        "[career_agent] Report: %d chars, %d project suggestions",
        len(report), len(project_ideas),
    )
    return report, project_ideas
