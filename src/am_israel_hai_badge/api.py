from __future__ import annotations

import csv
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Jerusalem")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Tzevaadom API
_TZEVA_ALERTS_BASE = "https://api.tzevaadom.co.il/alerts-history/id"
_TZEVA_MSGS_BASE = "https://api.tzevaadom.co.il/system-messages/id"
_CITIES_JSON_URL = (
    "https://raw.githubusercontent.com/peppermint-ice/how-the-lion-roars"
    "/refs/heads/main/cities.json"
)

# Local CSV cache — committed to repo, updated incrementally each run
_ALERTS_CSV = _PROJECT_ROOT / "data" / "tzevaadom_alerts.csv"
_MESSAGES_CSV = _PROJECT_ROOT / "data" / "tzevaadom_messages.csv"
_CSV_HEADER = ["time", "city", "id", "category", "title"]

# Known-good IDs as of 2026-03-27 — used as floor for forward-probing the max
_ALERTS_ID_FLOOR = 6700
_MSGS_ID_FLOOR = 1300

_TIMEOUT = 10
_REQUEST_DELAY = 0.5   # pause after a real (200) response
_SKIP_DELAY = 0.2      # pause after a 404 (also helps avoid rate limits during gap scans)
_RATE_LIMIT_BACKOFF = 10  # pause on 429 before retrying
_SCAN_WINDOW_DAYS = 32
_BACKFILL_ALERT_WINDOW = 2000  # IDs to scan back during initial backfill (~50 days at current rate)
_BACKFILL_MSG_WINDOW = 600     # messages are sparser: ~15/day * 40 days of buffer
_CONSECUTIVE_OLD_STOP = 15  # stop initial backfill after this many real-but-old records

# Tzevaadom threat → oref category
_THREAT_TO_CAT: dict[int, int] = {0: 1, 5: 2}

# English title substrings that identify message type
_EW_TITLES = ("Early Warning", "Staying near protected space")
_AC_TITLES = ("Incident Ended", "Leaving the protected space")

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class FetchError(Exception):
    """Raised when a required API fetch fails."""


# --------------------------------------------------------------------------- #
# Low-level HTTP                                                               #
# --------------------------------------------------------------------------- #

def _http_get(url: str) -> tuple[int, bytes]:
    """Return (status_code, body). Retries once on 429. Never raises."""
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                logger.debug("Rate limited, backing off %ds", _RATE_LIMIT_BACKOFF)
                time.sleep(_RATE_LIMIT_BACKOFF)
                continue
            return exc.code, b""
        except Exception:
            return 0, b""
    return 429, b""


def _fetch_json(url: str) -> dict | list | None:
    status, body = _http_get(url)
    if status == 200 and body:
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            pass
    return None


def _find_api_max(base_url: str, floor: int) -> int:
    """Find the current API max by probing forward from a known floor ID.

    If floor itself returns 404, scans backward to find a valid starting point.
    Then probes forward until the first 404.
    """
    # Verify the floor is valid
    status, _ = _http_get(f"{base_url}/{floor}")
    if status != 200:
        # Floor is too high — scan backward to find a real ID
        found = False
        for candidate in range(floor - 1, max(1, floor - 500), -1):
            s, _ = _http_get(f"{base_url}/{candidate}")
            if s == 200:
                floor = candidate
                found = True
                break
        if not found:
            return floor  # give up, return the estimate

    # Probe forward to find the true max
    current = floor
    while True:
        status, _ = _http_get(f"{base_url}/{current + 1}")
        if status == 200:
            current += 1
        elif status == 0:
            time.sleep(1)
            s2, _ = _http_get(f"{base_url}/{current + 1}")
            current = current + 1 if s2 == 200 else current
            if s2 != 200:
                break
        else:
            break
    return current


# --------------------------------------------------------------------------- #
# CSV helpers                                                                  #
# --------------------------------------------------------------------------- #

def _ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_CSV_HEADER)


def _read_csv_max_id(path: Path) -> int:
    """Return the max value in the 'id' column. 0 if file is empty."""
    if not path.exists():
        return 0
    max_id = 0
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    max_id = max(max_id, int(row["id"]))
                except (ValueError, KeyError):
                    pass
    except Exception:
        pass
    return max_id


def _append_rows(path: Path, rows: list[list]) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def _read_records(path: Path, area_set: set[str], since: datetime) -> list[dict]:
    """Read CSV and return oref-compatible dicts for our area within the window."""
    if not path.exists():
        return []
    records = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("city") not in area_set:
                    continue
                try:
                    ts = datetime.fromisoformat(row["time"]).replace(tzinfo=_TZ)
                except Exception:
                    continue
                if ts < since:
                    continue
                records.append({
                    "alertDate": row["time"],
                    "category": int(row["category"]),
                    "category_desc": row.get("title", ""),
                    "data": row["city"],
                    "rid": f"{path.stem}_{row['id']}",
                })
    except Exception as exc:
        logger.warning("Error reading %s: %s", path, exc)
    return records


# --------------------------------------------------------------------------- #
# City ID map (needed for system-messages which use integer city IDs)          #
# --------------------------------------------------------------------------- #

def _load_city_id_map(
    area_names: list[str],
) -> tuple[dict[str, int], dict[int, str]]:
    """Return ({name: id}, {id: name}) for requested area names."""
    data = _fetch_json(_CITIES_JSON_URL)
    if not isinstance(data, dict):
        raise FetchError("Failed to load cities.json")
    name_to_id: dict[str, int] = {}
    for name, info in data.get("cities", {}).items():
        if name in area_names:
            name_to_id[name] = info["id"]
    missing = [n for n in area_names if n not in name_to_id]
    if missing:
        logger.warning("Areas not found in cities.json: %s", missing)
    return name_to_id, {v: k for k, v in name_to_id.items()}


# --------------------------------------------------------------------------- #
# Alert CSV update                                                             #
# --------------------------------------------------------------------------- #

def _rows_from_alert_id(alert_id: int, area_set: set[str]) -> list[list]:
    """Fetch one alert ID and return CSV rows for our area."""
    data = _fetch_json(f"{_TZEVA_ALERTS_BASE}/{alert_id}")
    if not data:
        return []
    rows = []
    for wave in data.get("alerts", []):
        cat = _THREAT_TO_CAT.get(wave.get("threat"))
        if cat is None:
            continue
        ts = datetime.fromtimestamp(wave["time"], tz=_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        title = "ירי רקטות וטילים" if wave.get("threat") == 0 else "חדירת כלי טיס עוין"
        for city in wave.get("cities", []):
            if city in area_set:
                rows.append([ts, city, alert_id, cat, title])
    return rows


def _update_alerts_csv(
    path: Path, local_max: int, api_max: int, area_set: set[str], since: datetime
) -> None:
    if local_max == api_max:
        logger.info("Alerts: up to date (max=%d)", api_max)
        return

    if local_max == 0:
        # Initial backfill: scan backward from api_max, stop when past window
        logger.info("Alerts: initial backfill from ID %d", api_max)
        buffer: list[list] = []
        consecutive_old = 0
        for alert_id in range(api_max, max(1, api_max - _BACKFILL_ALERT_WINDOW), -1):
            data = _fetch_json(f"{_TZEVA_ALERTS_BASE}/{alert_id}")
            if data is None:
                time.sleep(_SKIP_DELAY)
                continue
            time.sleep(_REQUEST_DELAY)
            has_recent = False
            for wave in data.get("alerts", []):
                cat = _THREAT_TO_CAT.get(wave.get("threat"))
                if cat is None:
                    continue
                ts_dt = datetime.fromtimestamp(wave["time"], tz=_TZ)
                if ts_dt < since:
                    continue
                has_recent = True
                ts = ts_dt.strftime("%Y-%m-%dT%H:%M:%S")
                title = "ירי רקטות וטילים" if wave.get("threat") == 0 else "חדירת כלי טיס עוין"
                for city in wave.get("cities", []):
                    if city in area_set:
                        buffer.append([ts, city, alert_id, cat, title])
            if not has_recent:
                consecutive_old += 1
                if consecutive_old >= _CONSECUTIVE_OLD_STOP:
                    logger.info("  alert backfill: early stop at ID %d", alert_id)
                    break
            else:
                consecutive_old = 0
        buffer.sort(key=lambda r: r[0])
        _append_rows(path, buffer)
        logger.info("  backfilled %d alert rows", len(buffer))
    else:
        # Incremental: fetch only new IDs forward
        logger.info("Alerts: fetching IDs %d → %d", local_max + 1, api_max)
        new_rows = 0
        for alert_id in range(local_max + 1, api_max + 1):
            rows = _rows_from_alert_id(alert_id, area_set)
            if rows:
                _append_rows(path, rows)
                new_rows += len(rows)
            time.sleep(_REQUEST_DELAY if rows else _SKIP_DELAY)
        logger.info("  appended %d new alert rows", new_rows)


# --------------------------------------------------------------------------- #
# Messages CSV update                                                          #
# --------------------------------------------------------------------------- #

def _rows_from_msg_id(
    msg_id: int, city_ids: set[int], id_to_name: dict[int, str], area_set: set[str]
) -> list[list]:
    """Fetch one system-message ID and return CSV rows for our area."""
    if msg_id == 195:
        return []
    data = _fetch_json(f"{_TZEVA_MSGS_BASE}/{msg_id}")
    if not data or not data.get("time"):
        return []
    ts = datetime.fromtimestamp(data["time"], tz=_TZ).strftime("%Y-%m-%dT%H:%M:%S")
    title = data.get("titleEn") or ""
    if any(t in title for t in _EW_TITLES):
        category = 14
    elif any(t in title for t in _AC_TITLES):
        category = 13
    elif data.get("instruction"):
        category = 14
    else:
        return []
    msg_city_ids = set(data.get("citiesIds", []))
    if 10000000 in msg_city_ids:
        cities = list(area_set)
    else:
        cities = [id_to_name[cid] for cid in msg_city_ids & city_ids if cid in id_to_name]
    return [[ts, city, msg_id, category, title] for city in cities]


def _update_messages_csv(
    path: Path,
    local_max: int,
    api_max: int,
    city_ids: set[int],
    id_to_name: dict[int, str],
    area_set: set[str],
    since: datetime,
) -> None:
    if local_max == api_max:
        logger.info("Messages: up to date (max=%d)", api_max)
        return

    if local_max == 0:
        # Initial backfill: scan backward from api_max
        logger.info("Messages: initial backfill from ID %d", api_max)
        buffer: list[list] = []
        consecutive_old = 0
        for msg_id in range(api_max, max(1, api_max - _BACKFILL_MSG_WINDOW), -1):
            if msg_id == 195:
                continue
            data = _fetch_json(f"{_TZEVA_MSGS_BASE}/{msg_id}")
            if data is None:
                time.sleep(_SKIP_DELAY)
                continue
            time.sleep(_REQUEST_DELAY)
            if not data.get("time"):
                continue
            ts_dt = datetime.fromtimestamp(data["time"], tz=_TZ)
            if ts_dt < since:
                consecutive_old += 1
                if consecutive_old >= 10:
                    logger.info("  msg backfill: early stop at ID %d", msg_id)
                    break
                continue
            consecutive_old = 0
            buffer.extend(_rows_from_msg_id(msg_id, city_ids, id_to_name, area_set))
        buffer.sort(key=lambda r: r[0])
        _append_rows(path, buffer)
        logger.info("  backfilled %d message rows", len(buffer))
    else:
        # Incremental
        logger.info("Messages: fetching IDs %d → %d", local_max + 1, api_max)
        new_rows = 0
        for msg_id in range(local_max + 1, api_max + 1):
            rows = _rows_from_msg_id(msg_id, city_ids, id_to_name, area_set)
            if rows:
                _append_rows(path, rows)
                new_rows += len(rows)
            time.sleep(_REQUEST_DELAY if rows else _SKIP_DELAY)
        logger.info("  appended %d new message rows", new_rows)


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def fetch_all_areas_history(area_names: list[str]) -> list[dict]:
    """Incrementally update CSV cache and return recent records.

    On first run (empty CSVs): backfills the last 32 days.
    On subsequent runs: only fetches IDs newer than what's already cached.
    """
    since = datetime.now(tz=_TZ) - timedelta(days=_SCAN_WINDOW_DAYS)
    area_set = set(area_names)

    # City ID map for system-messages (uses integer city IDs)
    try:
        name_to_id, id_to_name = _load_city_id_map(area_names)
        city_ids = set(name_to_id.values())
    except FetchError as exc:
        logger.warning("City map unavailable (%s) — system messages skipped", exc)
        city_ids = set()
        id_to_name = {}

    _ensure_csv(_ALERTS_CSV)
    _ensure_csv(_MESSAGES_CSV)

    # --- Update alerts ---
    local_alerts_max = _read_csv_max_id(_ALERTS_CSV)
    api_alerts_max = _find_api_max(
        _TZEVA_ALERTS_BASE, max(local_alerts_max, _ALERTS_ID_FLOOR)
    )
    logger.info("Alerts: local=%d  api=%d", local_alerts_max, api_alerts_max)
    _update_alerts_csv(_ALERTS_CSV, local_alerts_max, api_alerts_max, area_set, since)

    # --- Update messages ---
    if city_ids:
        local_msgs_max = _read_csv_max_id(_MESSAGES_CSV)
        api_msgs_max = _find_api_max(
            _TZEVA_MSGS_BASE, max(local_msgs_max, _MSGS_ID_FLOOR)
        )
        logger.info("Messages: local=%d  api=%d", local_msgs_max, api_msgs_max)
        _update_messages_csv(
            _MESSAGES_CSV, local_msgs_max, api_msgs_max,
            city_ids, id_to_name, area_set, since
        )
    else:
        logger.warning("No city IDs — system messages skipped")

    alerts = _read_records(_ALERTS_CSV, area_set, since)
    messages = _read_records(_MESSAGES_CSV, area_set, since)
    all_records = alerts + messages
    logger.info(
        "Returning %d records (%d alerts + %d messages)",
        len(all_records), len(alerts), len(messages),
    )
    return all_records


def fetch_github_commit_count(username: str, days: int = 30) -> int:
    """Count commits for a GitHub user in the last N days via GraphQL API."""
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
