# Ideation Agentic System

A Discord-native personal assistant powered by **Gemini 2.5 Flash** and **LangGraph**, evolving from idea capture to career development — all within Discord.

---

## What it does

**Drop an idea in `#ideas`** → Bot spins up a thread, starts a ReAct conversation, scores the idea hourly, and includes it in your 9AM morning brief.

**Drop your resume in `#career`** → Career ReAct agent researches the market, identifies skill gaps, recommends certifications + resources, and seeds portfolio project ideas into `#ideas`.

**Every morning at 9AM** → A Google Calendar event is created with your top project idea, today's schedule, and idea pipeline stats. You get a notification at 8:55AM.

---

## Architecture

```
ideation-agentic-system/
├── discord_bot/           # Bot interface (main.py, config.py)
│
├── agents/
│   ├── ideation/          # LangGraph ReAct — web search, URL fetch, doc summarize
│   ├── career/            # LangGraph ReAct — autonomous Tavily market research
│   └── tracker/           # Hourly scoring pipeline (portfolio + activity scores)
│
├── tools/                 # Shared across agents
│   ├── web_search.py      # Tavily wrapper
│   ├── document.py        # PDF extraction, URL fetch, summarization
│   └── ocr.py             # Gemini Vision OCR (scanned PDF fallback)
│
└── shared/                # Infrastructure
    ├── store.py            # SQLite (threads, ideas, career profiles)
    ├── llm.py              # Gemini 2.5 Flash via LangChain
    ├── calendar_client.py  # Google Calendar OAuth2 client
    └── brief.py            # Morning brief assembler
```

### Agent flows

**Ideation agent** (`agents/ideation/`)
- LangGraph ReAct loop: LLM decides when to call `search_web`, `fetch_url`, `read_attachment`, `summarize_document`
- Discord thread = persistent conversation memory

**Career agent** (`agents/career/`)
- LangGraph ReAct loop, single tool: `search_market` (Tavily)
- LLM reads the resume, *decides which searches to run*, iterates until it has enough data
- Produces skill gap report + project suggestions in one agentic pass

**Tracker** (`agents/tracker/`)
- Runs every hour
- Portfolio score (LLM, 1–10) × Activity score (recency + volume + momentum)

---

## Setup

### Prerequisites
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Google Cloud project with Calendar API enabled + `credentials.json`

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
```

> **Channel IDs:** Discord → Settings → Advanced → Enable Developer Mode → right-click channel → Copy Channel ID.

> **Google Calendar:** Download OAuth2 credentials (Desktop app) from Google Cloud Console as `credentials.json` in the project root. The OAuth browser flow runs automatically on first start.

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
| `#career` | Post your target role + attach resume PDF → career agent runs |

---

## Tech stack

`discord.py` · `LangGraph` · `langchain-google-genai` · `Gemini 2.5 Flash` · `Tavily` · `Google Calendar API` · `SQLite (aiosqlite)` · `pypdf` · `trafilatura`
