# Ideation Agentic System

A Discord-native personal assistant powered by **Gemini 2.5 Flash** and **LangGraph** that takes ideas from first thought to scaffolded GitHub repo — all within Discord.

---

## What it does

**Drop an idea in `#ideas`** → Bot spins up a thread, starts a ReAct conversation, and scores the idea hourly.

**Say "let's build this"** → Idea is queued for Antigravity via MCP. Antigravity scaffolds the GitHub repo, creates issues, and reports back. Bot posts the repo link and switches to GitHub commit tracking.

**Drop your resume in `#career`** → Career ReAct agent autonomously researches the market, identifies skill gaps, and seeds portfolio project ideas into `#ideas`.

**Every morning at 9AM** → Google Calendar event with what you're building (commit activity), your top unstarted idea, today's schedule, and pipeline stats.

---

## Architecture

```
ideation-agentic-system/
├── discord_bot/            # Bot interface (main.py, config.py)
│
├── agents/
│   ├── ideation/           # LangGraph ReAct — search, fetch, summarize, queue build
│   ├── career/             # LangGraph ReAct — autonomous Tavily market research
│   └── tracker/            # Hourly pipeline: Discord activity or GitHub commits
│
├── tools/                  # Shared across agents
│   ├── web_search.py       # Tavily wrapper
│   ├── document.py         # PDF extraction, URL fetch, summarization
│   ├── ocr.py              # Gemini Vision OCR (scanned PDF fallback)
│   ├── build.py            # queue_build_task — writes to MCP task queue
│   └── github.py           # GitHub commit activity polling
│
├── mcp_server/             # Stdio MCP server (spawned by Antigravity)
│   ├── server.py           # FastMCP tools: get_next_task, report_task_complete
│   └── cli.py              # Entry point: uv run python -m mcp_server.cli
│
└── shared/                 # Infrastructure
    ├── store.py            # SQLite (threads, ideas + lifecycle, build_tasks queue)
    ├── llm.py              # Gemini 2.5 Flash via LangChain
    ├── calendar_client.py  # Google Calendar OAuth2 client
    └── brief.py            # Morning brief assembler
```

### Agent flows

**Ideation agent** (`agents/ideation/`)
- LangGraph ReAct loop with tools: `search_web`, `fetch_url`, `read_attachment`, `summarize_document`, `queue_build_task`
- Discord thread = persistent conversation memory
- When user says "let's build" → agent calls `queue_build_task` → Antigravity picks it up via MCP

**Career agent** (`agents/career/`)
- LangGraph ReAct, single tool: `search_market` (Tavily)
- LLM reads the resume, *decides which searches to run*, iterates until it has enough data
- Produces skill gap report + project suggestions in one agentic pass

**Tracker** (`agents/tracker/`)
- Runs every hour
- **Ideating ideas**: Portfolio score (LLM, 1–10) + Activity score (Discord message recency/volume)
- **Building ideas**: Activity score = GitHub commit frequency (last 7 days)

### Idea lifecycle

```
ideating → (queue_build_task called) → Antigravity scaffolds repo
         → building → tracker polls GitHub commits → brief shows commit count
```

### Antigravity ↔ Bot via MCP

```
Bot (planner)                         Antigravity (executor)
──────────────────────────────────    ──────────────────────────────────
queue_build_task() → DB               polls get_next_task() via MCP
                                      ↓ scaffolds repo, creates issues
                                      report_task_complete(task_id, repo_url)
                                      ↓
                    DB updated ← ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
30s poller detects completion
→ "🚀 Repo is live. Go forth, padawan."
```

---

## Setup

### Prerequisites
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Google Cloud project with Calendar API enabled + `credentials.json`
- [Antigravity](https://antigravity.dev) with MCP support (for repo scaffolding)

### Install

```bash
git clone <repo-url>
cd ideation-agentic-system
uv sync
```

### Environment variables

```bash
cp .env.example .env
```

```env
DISCORD_TOKEN=your_bot_token
GEMINI_API_KEY=your_gemini_api_key
AGENT_CHANNEL_ID=        # #ideas channel ID
CAREER_CHANNEL_ID=       # #career channel ID
TAVILY_API_KEY=your_tavily_key
GITHUB_TOKEN=            # Optional: read-only PAT for commit tracking
MCP_PORT=8765            # Optional: default 8765
```

> **Google Calendar:** Download OAuth2 credentials (Desktop app) from Google Cloud Console as `credentials.json` in project root. OAuth browser flow runs on first start.

### Connect Antigravity

Add to your Antigravity `mcp_config.json`:

```json
{
    "mcpServers": {
        "ideation-system": {
            "command": "uv",
            "args": [
                "--directory", "/absolute/path/to/ideation-agentic-system",
                "run", "python", "-m", "mcp_server.cli"
            ]
        }
    }
}
```

Then give Antigravity a standing prompt: *"Continuously poll `get_next_task` from the ideation-system MCP server. When a task is returned, scaffold the GitHub repo, push an initial commit, create issues from the idea summary, then call `report_task_complete`."*

### Run

```bash
uv run python -m discord_bot.main
```

---

## Usage

| Channel | Action |
|---|---|
| `#ideas` | Post any idea → bot creates a thread and starts a conversation |
| `#ideas` thread | Continue the conversation — no mention needed |
| `#ideas` thread | Say "let's build this" → queues repo scaffolding for Antigravity |
| `#career` | Post your target role + attach resume PDF → career agent runs |

---

## Tech stack

`discord.py` · `LangGraph` · `langchain-google-genai` · `Gemini 2.5 Flash` · `Tavily` · `Google Calendar API` · `GitHub REST API` · `MCP (mcp SDK)` · `FastAPI` · `SQLite (aiosqlite)` · `pypdf` · `trafilatura`
