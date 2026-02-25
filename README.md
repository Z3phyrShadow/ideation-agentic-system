# Discord AI Assistant

A production-ready MVP Discord bot powered by **Gemini 2.5 Flash**, orchestrated via **LangGraph**, with Discord threads as persistent conversation logs and SQLite-backed thread tracking.

---

## Architecture

```
discord_agent/
├── main.py           # Entry point — Discord client, slash commands, events
├── graph.py          # LangGraph definition (START → agent_node → END)
├── llm.py            # Gemini 2.5 Flash initialization via LangChain adapter
├── memory.py         # Reconstructs message history from Discord thread
├── store.py          # SQLite persistence for bot-created thread IDs
├── config.py         # Environment variable loading and validation
└── system_prompt.txt # Agent personality prompt
```

### How it works

1. User runs `/chat <message>` — bot creates a new thread in the configured channel.
2. The opening message is sent through LangGraph → Gemini → response posted in thread.
3. Every subsequent message in the thread triggers the bot to:
   - Fetch the last 30 messages from the thread.
   - Reconstruct LangChain message history (SystemMessage + HumanMessage/AIMessage).
   - Invoke LangGraph → Gemini.
   - Post the response.
4. Thread IDs are stored in `threads.db` (SQLite) — the bot resumes responding to all previous threads after a restart.

---

## Setup

### 1. Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) installed

### 2. Clone and install dependencies

```bash
git clone <repo-url>
cd ideation-agentic-system
uv sync
```

### 3. Create a Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application → add a **Bot**.
3. Under **Bot**, enable:
   - `MESSAGE CONTENT INTENT`
   - `SERVER MEMBERS INTENT`
4. Under **OAuth2 → URL Generator**, select scopes: `bot`, `applications.commands`.
5. Select bot permissions: `Send Messages`, `Create Public Threads`, `Read Message History`.
6. Copy the generated URL and invite the bot to your server.
7. Copy the **Bot Token**.

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_TOKEN=your_bot_token
GEMINI_API_KEY=your_gemini_api_key
AGENT_CHANNEL_ID=123456789012345678  # Channel where threads are created
```

> **Getting `AGENT_CHANNEL_ID`:** In Discord, enable Developer Mode (Settings → Advanced), then right-click the target channel and select **Copy Channel ID**.

> **Getting `GEMINI_API_KEY`:** Visit [Google AI Studio](https://aistudio.google.com/app/apikey).

### 5. Run the bot

```bash
uv run python -m discord_agent.main
```

---

## Usage

| Action | How |
|---|---|
| Start a new conversation | `/chat <your message>` in any server channel |
| Continue a conversation | Just type in the bot's thread — no mention needed |
| New session after restart | Bot automatically resumes all previous threads |

---

## Design Decisions

| Decision | Rationale |
|---|---|
| Discord threads as memory | Zero-infrastructure conversation log; no DB needed for messages |
| SQLite (`threads.db`) | Lightweight persistence for thread IDs; survives restarts without external services |
| `asyncio.to_thread` for LangGraph | LangGraph's `.invoke()` is sync; wrapping prevents blocking the async event loop |
| `langchain-google-genai` adapter | Proper LangChain/LangGraph integration with `BaseMessage`-compatible types |
| System prompt from file | Allows editing personality without touching code |

---

## Extending the Agent

To add new nodes to the LangGraph pipeline (e.g., planner, tools, evaluator), edit `graph.py`:

```python
builder.add_node("planner", planner_node)
builder.add_edge(START, "planner")
builder.add_edge("planner", "agent")
builder.add_edge("agent", END)
```

The `run_graph()` interface in `graph.py` and all calling code in `main.py` remain unchanged.
