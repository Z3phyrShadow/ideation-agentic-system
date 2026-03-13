"""
github.py
---------
GitHub API utilities for commit activity tracking.

Public API:
    fetch_github_activity(repo_url) -> int
        Returns the number of commits in the last 7 days.
"""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("tools.github")


def _parse_owner_repo(repo_url: str) -> tuple[str, str] | None:
    """
    Parse owner and repo name from a GitHub URL.

    Supports:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        github.com/owner/repo
    """
    url = repo_url.strip().rstrip("/").removesuffix(".git")
    # Find github.com/owner/repo pattern
    if "github.com" not in url:
        return None
    parts = url.split("github.com/")[-1].split("/")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def fetch_github_activity(repo_url: str) -> int:
    """
    Return the number of commits pushed to the default branch in the last 7 days.

    Uses the GitHub REST API. Requires GITHUB_TOKEN env var for auth
    (works on public repos without it, but rate limits are tighter).

    Args:
        repo_url: Full GitHub repository URL.

    Returns:
        Commit count (int). Returns 0 on error or if repo not found.
    """
    parsed = _parse_owner_repo(repo_url)
    if not parsed:
        log.warning("[github] Could not parse owner/repo from URL: %s", repo_url)
        return 0

    owner, repo = parsed
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    try:
        import httpx
        from discord_bot.config import GITHUB_TOKEN

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits",
                params={"since": since, "per_page": 100},
                headers=headers,
            )

        if resp.status_code == 404:
            log.warning("[github] Repo not found: %s/%s", owner, repo)
            return 0
        resp.raise_for_status()

        count = len(resp.json())
        log.info("[github] %s/%s — %d commits in last 7 days", owner, repo, count)
        return count

    except Exception as exc:
        log.warning("[github] fetch_github_activity failed for %s: %s", repo_url, exc)
        return 0
