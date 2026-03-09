"""
calendar_client.py
------------------
Google Calendar integration.

Public API:
    get_service()                       — authenticated Calendar API client (OAuth2)
    get_todays_events()                 — list of today's calendar events for the morning brief
    create_morning_brief_event(content) — create/update a 9AM daily brief event in Calendar

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
            try:
                log.info("[calendar] Refreshing expired OAuth token.")
                creds.refresh(Request())
            except Exception as refresh_err:
                log.warning(
                    "[calendar] Token refresh failed (%s). Deleting token.json and re-running OAuth flow.",
                    refresh_err,
                )
                _TOKEN_PATH.unlink(missing_ok=True)
                creds = None

        if not creds or not creds.valid:
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
# Today's events (used by the morning brief)
# ---------------------------------------------------------------------------


def get_todays_events() -> list[dict]:
    """
    Return a list of today's Google Calendar events sorted by start time.

    Each dict has:
        summary    — event title
        start_time — human-readable local time string (e.g. '9:00 AM')

    Returns an empty list if credentials are missing or an error occurs.
    """
    try:
        service = get_service()
    except FileNotFoundError as exc:
        log.warning("[calendar] Skipping event fetch: %s", exc)
        return []
    except Exception:
        log.exception("[calendar] Failed to get Calendar service")
        return []

    local_tz = datetime.now().astimezone().tzinfo
    now_local = datetime.now(local_tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    try:
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception:
        log.exception("[calendar] Failed to list today's events")
        return []

    events = []
    for item in result.get("items", []):
        summary = item.get("summary", "(No title)")
        start = item.get("start", {})
        # dateTime for timed events, date for all-day events
        if "dateTime" in start:
            dt = datetime.fromisoformat(start["dateTime"])
            time_str = dt.strftime("%-I:%M %p") if hasattr(dt, 'strftime') else dt.strftime("%I:%M %p").lstrip("0")
            # Windows-safe fallback
            try:
                time_str = dt.strftime("%-I:%M %p")
            except ValueError:
                time_str = dt.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
        else:
            time_str = "All day"
        events.append({"summary": summary, "start_time": time_str})

    log.info("[calendar] Fetched %d events for today.", len(events))
    return events


# ---------------------------------------------------------------------------
# Morning brief Calendar event
# ---------------------------------------------------------------------------

_BRIEF_EVENT_TITLE = "🌅 Morning Brief"
_BRIEF_DURATION_MINUTES = 15
_BRIEF_HOUR = 9  # 9 AM local time


def create_morning_brief_event(content: str) -> None:
    """
    Create or update today's '🌅 Morning Brief' event at 9 AM with the brief
    content in its description. A 5-minute popup reminder is set so you get
    a notification at 8:55 AM.

    Args:
        content: The formatted brief text to place in the event description.
    """
    try:
        service = get_service()
    except FileNotFoundError as exc:
        log.warning("[calendar] Skipping brief event creation: %s", exc)
        return
    except Exception:
        log.exception("[calendar] Failed to get Calendar service for brief")
        return

    local_tz = datetime.now().astimezone().tzinfo
    now_local = datetime.now(local_tz)
    start = now_local.replace(hour=_BRIEF_HOUR, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=_BRIEF_DURATION_MINUTES)

    # Check if today's brief event already exists
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    try:
        existing_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                q=_BRIEF_EVENT_TITLE,
                singleEvents=True,
            )
            .execute()
        )
    except Exception:
        log.exception("[calendar] Failed to search for existing brief event")
        return

    existing = next(
        (e for e in existing_result.get("items", []) if e.get("summary") == _BRIEF_EVENT_TITLE),
        None,
    )

    # Strip Discord markdown (**/##) for plain-text Calendar description
    import re
    plain_content = re.sub(r"\*+", "", content)  # remove bold markers

    if existing is None:
        event_body = {
            "summary": _BRIEF_EVENT_TITLE,
            "description": plain_content,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 5}],
            },
        }
        try:
            created = service.events().insert(calendarId="primary", body=event_body).execute()
            log.info("[calendar] Created Morning Brief event: %s", created.get("htmlLink"))
        except Exception:
            log.exception("[calendar] Failed to create Morning Brief event")
    else:
        try:
            service.events().patch(
                calendarId="primary",
                eventId=existing["id"],
                body={"description": plain_content},
            ).execute()
            log.info("[calendar] Updated Morning Brief event (id=%s)", existing["id"])
        except Exception:
            log.exception("[calendar] Failed to update Morning Brief event")
