from __future__ import annotations

import csv
import json
import logging
import os
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

# Upstream bootstrap: forks download from central repo on first run instead of
# doing a full 20-min backfill from the API.
_CENTRAL_REPO = "EydlinIlya/am-israel-hai-badge"
_IS_CENTRAL = os.environ.get("GITHUB_REPOSITORY", "") == _CENTRAL_REPO
_UPSTREAM_REPO = os.environ.get("UPSTREAM_REPO", "").strip()


def _upstream_url(filename: str) -> str | None:
    """Return upstream raw URL for a data file, or None if we are the upstream."""
    repo = _UPSTREAM_REPO or (None if _IS_CENTRAL else _CENTRAL_REPO)
    if not repo:
        return None
    return f"https://raw.githubusercontent.com/{repo}/master/data/{filename}"


# Local CSV cache — committed to repo, updated incrementally each run.
# DATA_DIR env var allows the web service to point at a persistent volume.
_DATA_DIR = Path(os.environ.get("DATA_DIR", str(_PROJECT_ROOT / "data")))
_ALERTS_CSV = _DATA_DIR / "tzevaadom_alerts.csv"
_MESSAGES_CSV = _DATA_DIR / "tzevaadom_messages.csv"
_CSV_HEADER = ["time", "city", "id", "category", "title"]

# Known-good IDs as of 2026-03-27 — used as floor for forward-probing the max
_ALERTS_ID_FLOOR = 6700
_MSGS_ID_FLOOR = 1300

_TIMEOUT = 10
_DOWNLOAD_TIMEOUT = 60  # longer timeout for upstream CSV download (~2 MB)
_REQUEST_DELAY = 0.5   # pause after a real (200) response
_SKIP_DELAY = 0.2      # pause after a 404
_RATE_LIMIT_BACKOFF = 10  # pause on 429 before retrying
_SCAN_WINDOW_DAYS = 32
_BACKFILL_ALERT_WINDOW = 2000  # IDs to scan back during initial backfill
_BACKFILL_MSG_WINDOW = 1500    # message IDs to scan back
_CONSECUTIVE_OLD_STOP = 15

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

def _http_get(url: str, timeout: int = _TIMEOUT) -> tuple[int, bytes]:
    """Return (status_code, body). Retries once on 429. Never raises."""
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    """Find the current API max by probing forward from a known floor ID."""
    status, _ = _http_get(f"{base_url}/{floor}")
    if status != 200:
        found = False
        for candidate in range(floor - 1, max(1, floor - 500), -1):
            s, _ = _http_get(f"{base_url}/{candidate}")
            if s == 200:
                floor = candidate
                found = True
                break
        if not found:
            return floor

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


def _read_records(
    path: Path, area_set: set[str] | None, since: datetime,
) -> list[dict]:
    """Read CSV and return oref-compatible dicts.

    When *area_set* is ``None`` every record is returned (no area filter,
    broadcast rows kept as-is with ``city="*"``).  When a set is given the
    old per-area filtering + broadcast expansion logic applies.
    """
    if not path.exists():
        return []
    records = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                city = row.get("city", "")
                try:
                    ts = datetime.fromisoformat(row["time"]).replace(tzinfo=_TZ)
                except Exception:
                    continue
                if ts < since:
                    continue

                # --- unfiltered mode (web service cache) ---
                if area_set is None:
                    records.append({
                        "alertDate": row["time"],
                        "category": int(row["category"]),
                        "category_desc": row.get("title", ""),
                        "data": city,
                        "rid": f"{path.stem}_{row['id']}",
                    })
                    continue

                # --- per-area filtered mode (CLI / Actions) ---
                if city not in area_set and city != "*":
                    continue
                if city == "*":
                    # Broadcast: expand to configured areas, but only for
                    # SAFETY (cat 13) signals.  Broadcast PREPARATORY (cat 14)
                    # messages would create phantom shelter sessions for areas
                    # that never actually had a siren.
                    cat = int(row["category"])
                    if cat != 13:
                        continue
                    for area in area_set:
                        records.append({
                            "alertDate": row["time"],
                            "category": cat,
                            "category_desc": row.get("title", ""),
                            "data": area,
                            "rid": f"{path.stem}_{row['id']}_{area}",
                        })
                else:
                    records.append({
                        "alertDate": row["time"],
                        "category": int(row["category"]),
                        "category_desc": row.get("title", ""),
                        "data": city,
                        "rid": f"{path.stem}_{row['id']}",
                    })
    except Exception as exc:
        logger.warning("Error reading %s: %s", path, exc)
    return records


def _download_upstream_csv(url: str, path: Path) -> bool:
    """Download CSV from upstream URL into path. Returns True on success."""
    logger.info("Bootstrapping %s from upstream...", path.name)
    status, body = _http_get(url, timeout=_DOWNLOAD_TIMEOUT)
    if status != 200 or not body:
        logger.warning("Upstream download failed (status=%d) — will backfill instead", status)
        return False
    text = body.decode("utf-8")
    first_line = text.split("\n")[0].strip()
    if first_line != ",".join(_CSV_HEADER):
        logger.warning("Upstream CSV header mismatch: %r", first_line)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    rows = max(0, text.count("\n") - 1)
    logger.info("  bootstrapped %d rows", rows)
    return True


# --------------------------------------------------------------------------- #
# City data (cities.json) — cached to avoid redundant fetches                  #
# --------------------------------------------------------------------------- #

_cities_cache: dict | None = None


def _fetch_cities_data() -> dict:
    """Fetch and cache the full cities.json dict."""
    global _cities_cache
    if _cities_cache is not None:
        return _cities_cache
    data = _fetch_json(_CITIES_JSON_URL)
    if not isinstance(data, dict):
        raise FetchError("Failed to load cities.json")
    _cities_cache = data.get("cities", data)
    return _cities_cache


def _load_all_city_map() -> dict[int, str]:
    """Return {city_id: city_name} for ALL cities in cities.json."""
    cities = _fetch_cities_data()
    id_to_name: dict[int, str] = {}
    for name, info in cities.items():
        if not isinstance(info, dict):
            continue
        cid = info.get("id", 0)
        if cid:
            id_to_name[cid] = name
    return id_to_name


def resolve_area_names(raw_names: list[str]) -> list[str]:
    """Resolve area names from any language (en/he/ru/ar) to Hebrew.

    Falls back to the original name if no match is found or if
    cities.json is unavailable.
    """
    try:
        cities = _fetch_cities_data()
    except Exception:
        logger.warning("Could not load cities.json — using area names as-is")
        return raw_names

    # Build lookup tables: exact match + case-insensitive
    exact: dict[str, str] = {}
    lower: dict[str, str] = {}
    for he_name, info in cities.items():
        if not isinstance(info, dict):
            continue
        exact[he_name] = he_name
        for lang in ("he", "en", "ru", "ar", "value"):
            val = info.get(lang, "")
            if val:
                exact.setdefault(val, he_name)
                lower.setdefault(val.lower(), he_name)

    resolved: list[str] = []
    seen: set[str] = set()
    for name in raw_names:
        he = exact.get(name) or lower.get(name.lower())
        if he is None:
            logger.warning("Area %r not found in cities.json — using as-is", name)
            he = name
        if he not in seen:
            resolved.append(he)
            seen.add(he)
    return resolved


# --------------------------------------------------------------------------- #
# Alert CSV update                                                             #
# --------------------------------------------------------------------------- #

def _rows_from_alert_id(alert_id: int) -> list[list]:
    """Fetch one alert ID and return CSV rows for ALL cities."""
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
            rows.append([ts, city, alert_id, cat, title])
    return rows


def _update_alerts_csv(
    path: Path, local_max: int, api_max: int, since: datetime
) -> None:
    if local_max == api_max:
        logger.info("Alerts: up to date (max=%d)", api_max)
        return

    if local_max == 0:
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
        logger.info("Alerts: fetching IDs %d → %d", local_max + 1, api_max)
        new_rows = 0
        for alert_id in range(local_max + 1, api_max + 1):
            rows = _rows_from_alert_id(alert_id)
            if rows:
                _append_rows(path, rows)
                new_rows += len(rows)
            time.sleep(_REQUEST_DELAY if rows else _SKIP_DELAY)
        logger.info("  appended %d new alert rows", new_rows)


# --------------------------------------------------------------------------- #
# Messages CSV update                                                          #
# --------------------------------------------------------------------------- #

def _rows_from_msg_id(msg_id: int, id_to_name: dict[int, str]) -> list[list]:
    """Fetch one system-message ID and return CSV rows for ALL cities.

    Broadcasts (city ID 10000000) are stored as city="*" — one row only.
    """
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
        return [[ts, "*", msg_id, category, title]]
    cities = [id_to_name[cid] for cid in msg_city_ids if cid in id_to_name]
    return [[ts, city, msg_id, category, title] for city in cities]


def _update_messages_csv(
    path: Path,
    local_max: int,
    api_max: int,
    id_to_name: dict[int, str],
    since: datetime,
) -> None:
    if local_max == api_max:
        logger.info("Messages: up to date (max=%d)", api_max)
        return

    if local_max == 0:
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
            buffer.extend(_rows_from_msg_id(msg_id, id_to_name))
        buffer.sort(key=lambda r: r[0])
        _append_rows(path, buffer)
        logger.info("  backfilled %d message rows", len(buffer))
    else:
        logger.info("Messages: fetching IDs %d → %d", local_max + 1, api_max)
        new_rows = 0
        for msg_id in range(local_max + 1, api_max + 1):
            rows = _rows_from_msg_id(msg_id, id_to_name)
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

    Central repo: backfills all cities from API on first run (~20 min, once ever).
    Forks: bootstrap from central repo CSVs on first run (seconds).
    Subsequent runs: only fetch new IDs (fast).
    CSVs store ALL cities — changing configured area needs no resync.
    """
    since = datetime.now(tz=_TZ) - timedelta(days=_SCAN_WINDOW_DAYS)
    area_set = set(area_names)

    # Load ALL city IDs for system-messages
    try:
        id_to_name = _load_all_city_map()
        missing = [n for n in area_names if n not in id_to_name.values()]
        if missing:
            logger.warning("Areas not found in cities.json: %s", missing)
    except FetchError as exc:
        logger.warning("City map unavailable (%s) — system messages skipped", exc)
        id_to_name = {}

    _ensure_csv(_ALERTS_CSV)
    _ensure_csv(_MESSAGES_CSV)

    # Bootstrap from upstream if CSVs are empty (fork first run / after resync)
    for path in (_ALERTS_CSV, _MESSAGES_CSV):
        if _read_csv_max_id(path) == 0:
            url = _upstream_url(path.name)
            if url:
                _download_upstream_csv(url, path)

    # --- Update alerts ---
    local_alerts_max = _read_csv_max_id(_ALERTS_CSV)
    api_alerts_max = _find_api_max(
        _TZEVA_ALERTS_BASE, max(local_alerts_max, _ALERTS_ID_FLOOR)
    )
    logger.info("Alerts: local=%d  api=%d", local_alerts_max, api_alerts_max)
    _update_alerts_csv(_ALERTS_CSV, local_alerts_max, api_alerts_max, since)

    # --- Update messages ---
    if id_to_name:
        local_msgs_max = _read_csv_max_id(_MESSAGES_CSV)
        api_msgs_max = _find_api_max(
            _TZEVA_MSGS_BASE, max(local_msgs_max, _MSGS_ID_FLOOR)
        )
        logger.info("Messages: local=%d  api=%d", local_msgs_max, api_msgs_max)
        _update_messages_csv(
            _MESSAGES_CSV, local_msgs_max, api_msgs_max, id_to_name, since
        )
    else:
        logger.warning("No city map — system messages skipped")

    alerts = _read_records(_ALERTS_CSV, area_set, since)
    messages = _read_records(_MESSAGES_CSV, area_set, since)
    all_records = alerts + messages
    logger.info(
        "Returning %d records (%d alerts + %d messages)",
        len(all_records), len(alerts), len(messages),
    )
    return all_records


def fetch_github_contribution_count(username: str, days: int = 30, token: str = "") -> int:
    """Count all GitHub contributions (commits, PRs, issues, reviews) in last N days.

    Uses ``contributionCalendar.totalContributions`` — the same number as the
    green squares on a GitHub profile.  Includes private activity when querying
    with the user's own OAuth token.
    """
    if not username:
        return 0

    if not token:
        import subprocess

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            try:
                token = subprocess.check_output(
                    ["gh", "auth", "token"], stderr=subprocess.DEVNULL
                ).decode().strip()
            except Exception:
                logger.warning("No GitHub token available, skipping contribution count")
                return 0

    now = datetime.now(tz=timezone.utc)
    from_date = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_date = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    query = json.dumps({"query": (
        '{ user(login: "' + username + '") {'
        '  contributionsCollection(from: "' + from_date + '", to: "' + to_date + '") {'
        "    contributionCalendar { totalContributions }"
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
        return cc["contributionCalendar"]["totalContributions"]
    except Exception as exc:
        logger.warning("GitHub GraphQL query failed: %s", exc)
        return 0


# Keep old name as alias for backwards compatibility with CLI workflow
fetch_github_commit_count = fetch_github_contribution_count


# --------------------------------------------------------------------------- #
# Web-service helpers                                                          #
# --------------------------------------------------------------------------- #

def update_csv_cache() -> None:
    """Incrementally update CSV files (alerts + messages) from the API.

    This is the same update logic as ``fetch_all_areas_history`` but without
    reading the records back — the web service worker calls this, then reads
    via ``read_all_cached_records``.
    """
    since = datetime.now(tz=_TZ) - timedelta(days=_SCAN_WINDOW_DAYS)

    try:
        id_to_name = _load_all_city_map()
    except FetchError as exc:
        logger.warning("City map unavailable (%s) — system messages skipped", exc)
        id_to_name = {}

    _ensure_csv(_ALERTS_CSV)
    _ensure_csv(_MESSAGES_CSV)

    for path in (_ALERTS_CSV, _MESSAGES_CSV):
        if _read_csv_max_id(path) == 0:
            url = _upstream_url(path.name)
            if url:
                _download_upstream_csv(url, path)

    local_alerts_max = _read_csv_max_id(_ALERTS_CSV)
    api_alerts_max = _find_api_max(
        _TZEVA_ALERTS_BASE, max(local_alerts_max, _ALERTS_ID_FLOOR)
    )
    _update_alerts_csv(_ALERTS_CSV, local_alerts_max, api_alerts_max, since)

    if id_to_name:
        local_msgs_max = _read_csv_max_id(_MESSAGES_CSV)
        api_msgs_max = _find_api_max(
            _TZEVA_MSGS_BASE, max(local_msgs_max, _MSGS_ID_FLOOR)
        )
        _update_messages_csv(
            _MESSAGES_CSV, local_msgs_max, api_msgs_max, id_to_name, since
        )


def read_all_cached_records(since: datetime | None = None) -> list[dict]:
    """Read ALL records from cached CSVs, unfiltered by area.

    Used by the web service to populate the in-memory AlertCache.
    """
    if since is None:
        since = datetime.now(tz=_TZ) - timedelta(days=_SCAN_WINDOW_DAYS)
    alerts = _read_records(_ALERTS_CSV, None, since)
    messages = _read_records(_MESSAGES_CSV, None, since)
    return alerts + messages
