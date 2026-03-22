from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_OREF_HISTORY_URL = (
    "https://alerts-history.oref.org.il"
    "/Shared/Ajax/GetAlarmsHistory.aspx"
)
_TIMEOUT = 30
_MAX_RETRIES = 3
_BACKOFF_BASE = 1  # seconds


class FetchError(Exception):
    """Raised when an API fetch fails after all retries."""


def _fetch_json(url: str) -> list[dict] | dict | None:
    """Fetch JSON from URL with retries and exponential backoff."""
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "am-israel-hai-badge/0.1"}
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
                # Official oref API returns UTF-8 with BOM
                text = raw.decode("utf-8-sig")
                return json.loads(text)
        except Exception as exc:
            wait = _BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "Fetch %s attempt %d failed: %s — retrying in %ds",
                url, attempt + 1, exc, wait,
            )
            time.sleep(wait)
    logger.error("Failed to fetch %s after %d attempts", url, _MAX_RETRIES)
    return None


def fetch_city_history(city: str) -> list[dict]:
    """Fetch full alert history for a single city from the official oref API.

    Uses mode=3 with city_0 parameter. Returns up to 3000 records per city,
    including all signal types (alerts, preparatory, safety).
    Raises FetchError on failure.
    """
    params = urllib.parse.urlencode(
        {"lang": "he", "mode": "3", "city_0": city},
        quote_via=urllib.parse.quote,
    )
    url = f"{_OREF_HISTORY_URL}?{params}"
    result = _fetch_json(url)
    if result is None:
        raise FetchError(f"Failed to fetch history for city {city!r}")
    if isinstance(result, list):
        return result
    return []


def fetch_all_areas_history(area_names: list[str]) -> list[dict]:
    """Fetch history for all configured area names and merge.

    Each area is fetched separately (mode=3&city_0=NAME), then
    results are merged and deduplicated by rid.
    """
    all_records: dict[int, dict] = {}  # rid -> record
    for name in area_names:
        records = fetch_city_history(name)
        for rec in records:
            rid = rec.get("rid")
            if rid is not None:
                all_records[rid] = rec
            else:
                all_records[id(rec)] = rec
        logger.info("  area %r: %d records", name, len(records))
    return list(all_records.values())


def fetch_github_commit_count(username: str, days: int = 30) -> int:
    """Count commits for a GitHub user in the last N days.

    Uses GitHub GraphQL API with contributionsCollection.
    Includes both public and private repo commits.
    Requires GITHUB_TOKEN env var or gh CLI auth.
    """
    if not username:
        return 0

    import os
    import subprocess

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        try:
            token = subprocess.check_output(
                ["gh", "auth", "token"], stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            logger.warning("No GitHub token available, skipping commit count")
            return 0

    now = datetime.now(tz=timezone.utc)
    from_date = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_date = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = json.dumps({"query": (
        '{ user(login: "' + username + '") {'
        '  contributionsCollection(from: "' + from_date + '", to: "' + to_date + '") {'
        "    totalCommitContributions"
        "    restrictedContributionsCount"
        "  }"
        "} }"
    )}).encode()

    try:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=query,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "am-israel-hai-badge/0.1",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        cc = data["data"]["user"]["contributionsCollection"]
        return cc["totalCommitContributions"] + cc["restrictedContributionsCount"]
    except Exception as exc:
        logger.warning("GitHub GraphQL query failed: %s", exc)
        return 0
