from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .api import fetch_day_history, fetch_history
from .badge import write_badge
from .config import load_area_names
from .normalize import normalize_day_history_alert, normalize_history_alert
from .shelter import compute_sessions, shelter_seconds_in_window, total_shelter_seconds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Jerusalem")
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_CACHE_PATH = _DATA_DIR / "daily_totals.json"
_MAX_DAYS = 31


def _load_cache() -> dict[str, float | None]:
    if _CACHE_PATH.exists():
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict[str, float | None]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _prune_cache(cache: dict[str, float | None], today: date) -> dict[str, float | None]:
    cutoff = (today - timedelta(days=_MAX_DAYS)).isoformat()
    return {k: v for k, v in cache.items() if k >= cutoff}


def _fetch_day_alerts(day: date, area_names: list[str]):
    """Fetch and normalize all alerts for a given day."""
    from .models import Alert

    alerts: list[Alert] = []

    raw_records = fetch_day_history(day)
    for rec in raw_records:
        alerts.extend(normalize_day_history_alert(rec))

    # For today, also merge the recent-history endpoint for freshest data
    if day == date.today():
        raw_recent = fetch_history()
        for rec in raw_recent:
            alerts.extend(normalize_history_alert(rec))

    return alerts


def run() -> None:
    area_names = load_area_names()
    logger.info("Area names: %s", area_names)

    now = datetime.now(tz=_TZ)
    today = now.date()
    yesterday = today - timedelta(days=1)

    cache = _load_cache()

    # --- Fetch today + yesterday (always fresh) ---
    recent_alerts = []
    for day in (yesterday, today):
        try:
            day_alerts = _fetch_day_alerts(day, area_names)
            recent_alerts.extend(day_alerts)
            logger.info("  %s: fetched %d alerts", day.isoformat(), len(day_alerts))
        except Exception:
            logger.exception("  %s: failed to fetch", day.isoformat())

    recent_sessions = compute_sessions(recent_alerts, area_names)

    # Compute today + yesterday totals from sessions
    today_start = datetime(today.year, today.month, today.day, tzinfo=_TZ)
    yesterday_start = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=_TZ)

    yesterday_seconds = shelter_seconds_in_window(recent_sessions, yesterday_start, today_start)
    today_seconds = shelter_seconds_in_window(recent_sessions, today_start, now)

    cache[yesterday.isoformat()] = yesterday_seconds
    cache[today.isoformat()] = None  # marker: always re-fetch
    cache[f"_today_seconds_{today.isoformat()}"] = today_seconds

    logger.info("  yesterday (%s): %.0fs, today (%s): %.0fs",
                yesterday.isoformat(), yesterday_seconds,
                today.isoformat(), today_seconds)

    # --- Fetch missing older days (2+ days ago) from cache ---
    days_to_fetch: list[date] = []
    for offset in range(2, 30):
        day = today - timedelta(days=offset)
        day_str = day.isoformat()
        if day_str not in cache or cache[day_str] is None:
            days_to_fetch.append(day)

    if days_to_fetch:
        logger.info("Fetching %d missing older days", len(days_to_fetch))
        for day in days_to_fetch:
            day_str = day.isoformat()
            try:
                alerts = _fetch_day_alerts(day, area_names)
                sessions = compute_sessions(alerts, area_names)
                cache[day_str] = total_shelter_seconds(sessions)
                logger.info("  %s: %.0f seconds", day_str, cache[day_str])
            except Exception:
                logger.exception("  %s: failed to compute", day_str)

    # Prune old entries
    cache = _prune_cache(cache, today)
    _save_cache(cache)

    # --- Compute period totals ---
    # 24h: rolling window clipped from sessions (today + yesterday alerts)
    window_24h_start = now - timedelta(hours=24)
    s_24h = shelter_seconds_in_window(recent_sessions, window_24h_start, now)

    # 7d / 30d: sum cached day totals for days 2..N-1, plus today + yesterday fresh
    def sum_older_days(start_offset: int, end_offset: int) -> float:
        total = 0.0
        for offset in range(start_offset, end_offset):
            day_str = (today - timedelta(days=offset)).isoformat()
            val = cache.get(day_str)
            if val is not None:
                total += val
        return total

    s_7d = today_seconds + yesterday_seconds + sum_older_days(2, 7)
    s_30d = today_seconds + yesterday_seconds + sum_older_days(2, 30)

    logger.info("Totals — 24h: %.0fs, 7d: %.0fs, 30d: %.0fs", s_24h, s_7d, s_30d)

    path = write_badge(s_24h, s_7d, s_30d)
    logger.info("Badge written to %s", path)


if __name__ == "__main__":
    run()
