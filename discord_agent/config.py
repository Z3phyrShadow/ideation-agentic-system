"""
config.py
---------
Loads environment variables from .env and exposes them as typed module-level
constants. Raises RuntimeError at import time if any required variable is
missing, so misconfiguration is caught immediately on startup.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """Return the value of an env var, or raise RuntimeError if missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Check your .env file."
        )
    return value


# Discord bot token (from Developer Portal)
DISCORD_TOKEN: str = _require("DISCORD_TOKEN")

# Google Generative AI API key
GEMINI_API_KEY: str = _require("GEMINI_API_KEY")

# The Discord channel ID where the bot creates new threads on /chat
AGENT_CHANNEL_ID: int = int(_require("AGENT_CHANNEL_ID"))
