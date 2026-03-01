"""
calendar_client.py
------------------
Google Calendar integration for the ideation tracker.

Provides two public callables:
    get_service()                              — authenticated Calendar API client
    upsert_7pm_reminder(title, summary) → None — create/update today's 7PM event

OAuth 2.0 flow (Desktop app):
    1. First run: browser consent window opens; approval writes token.json.
    2. Subsequent runs: token.json is loaded and auto-refreshed silently.

Prerequisites (one-time manual setup):
    - Google Cloud project with the Calendar API enabled.
    - OAuth 2.0 credentials (Desktop app type) downloaded as credentials.json
      into the project root.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("discord_agent.calendar_client")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"
_TOKEN_PATH = _PROJECT_ROOT / "token.json"

# ---------------------------------------------------------------------------
# OAuth scopes
# ---------------------------------------------------------------------------

_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ---------------------------------------------------------------------------
# Event constants
# ---------------------------------------------------------------------------

_EVENT_TITLE = "🚀 Project Time"
_EVENT_DURATION_MINUTES = 30
_EVENT_REMINDER_MINUTES = 10


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def get_service():
    """
    Return an authenticated Google Calendar API service object.

    Uses token.json for cached credentials (auto-refresh). On first run (or
    if token.json is missing/expired), launches a one-time browser OAuth flow
    and saves the resulting token to token.json.

    Raises:
        FileNotFoundError: if credentials.json is not present in the project root.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    if not _CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"credentials.json not found at {_CREDENTIALS_PATH}. "
            "Download OAuth 2.0 credentials (Desktop app) from Google Cloud Console "
            "and place them in the project root."
        )

    creds = None

    # Load cached token
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)

    # Refresh or re-authenticate if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("[calendar] Refreshing expired OAuth token.")
            creds.refresh(Request())
        else:
            log.info("[calendar] Launching browser OAuth consent flow (one-time setup).")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CREDENTIALS_PATH), _SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Persist the (new) token
        _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        log.info("[calendar] token.json saved to %s", _TOKEN_PATH)

    return build("calendar", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# 7 PM reminder logic
# ---------------------------------------------------------------------------


def _today_7pm_window() -> tuple[str, str]:
    """
    Return RFC 3339 start and end strings for today's 7:00 PM → 7:30 PM window
    in the local timezone.
    """
    local_tz = datetime.now().astimezone().tzinfo

    now_local = datetime.now(local_tz)
    start = now_local.replace(hour=19, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=_EVENT_DURATION_MINUTES)

    return start.isoformat(), end.isoformat()


def _find_todays_event(service, calendar_id: str = "primary") -> dict | None:
    """
    Search the primary calendar for an existing '🚀 Project Time' event today.

    Returns the first matching event dict, or None if not found.
    """
    local_tz = datetime.now().astimezone().tzinfo
    now_local = datetime.now(local_tz)

    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    events_result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            q=_EVENT_TITLE,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    for event in events_result.get("items", []):
        if event.get("summary", "") == _EVENT_TITLE:
            return event

    return None


def upsert_7pm_reminder(idea_title: str, idea_summary: str) -> None:
    """
    Create or update today's 7 PM 'Project Time' event with the top idea details.

    - If no event exists for today: creates a new 30-min event at 7 PM local time
      with a 10-min popup reminder.
    - If an event already exists: patches its description in-place.

    Args:
        idea_title:   Short name of the top-scoring project idea.
        idea_summary: 2–3 sentence description of the idea.
    """
    try:
        service = get_service()
    except FileNotFoundError as exc:
        log.warning("[calendar] Skipping Calendar update: %s", exc)
        return

    description = f"Top idea for today:\n\n**{idea_title}**\n\n{idea_summary}"
    start_str, end_str = _today_7pm_window()

    existing = _find_todays_event(service)

    if existing is None:
        # Create a new event
        event_body = {
            "summary": _EVENT_TITLE,
            "description": description,
            "start": {"dateTime": start_str},
            "end": {"dateTime": end_str},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": _EVENT_REMINDER_MINUTES},
                ],
            },
        }
        created = service.events().insert(calendarId="primary", body=event_body).execute()
        log.info(
            "[calendar] Created event '%s' at 7PM: %s",
            _EVENT_TITLE,
            created.get("htmlLink"),
        )
    else:
        # Patch the description of the existing event
        patch_body = {"description": description}
        service.events().patch(
            calendarId="primary",
            eventId=existing["id"],
            body=patch_body,
        ).execute()
        log.info(
            "[calendar] Updated description of existing event '%s' (id=%s)",
            _EVENT_TITLE,
            existing["id"],
        )
