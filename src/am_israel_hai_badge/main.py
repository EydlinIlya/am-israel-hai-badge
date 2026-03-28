from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .api import fetch_all_areas_history, fetch_github_commit_count
from .badge import write_badge
from .config import load_area_names, load_github_username
from .normalize import normalize_alert
from .shelter import compute_sessions, shelter_seconds_in_window
from .stats import write_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Jerusalem")


def run() -> None:
    area_names = load_area_names()
    logger.info("Area names: %s", area_names)

    now = datetime.now(tz=_TZ)

    # --- Fetch all history for our areas (single API call per area) ---
    try:
        raw_records = fetch_all_areas_history(area_names)
        logger.info("Fetched %d total records", len(raw_records))
    except Exception:
        logger.exception("API fetch failed — keeping existing badge unchanged")
        sys.exit(1)

    # Normalize to Alert objects
    alerts = []
    for rec in raw_records:
        try:
            alerts.extend(normalize_alert(rec))
        except Exception:
            logger.debug("Skipping bad record: %s", rec)

    logger.info("Normalized to %d alerts", len(alerts))

    # Compute shelter sessions
    sessions = compute_sessions(alerts, area_names)
    logger.info("Found %d shelter sessions", len(sessions))

    # --- Compute period totals ---
    s_24h = shelter_seconds_in_window(sessions, now - timedelta(hours=24), now)
    s_7d = shelter_seconds_in_window(sessions, now - timedelta(days=7), now)
    s_30d = shelter_seconds_in_window(sessions, now - timedelta(days=30), now)

    # --- Commit count ---
    github_user = load_github_username()
    commits_30d = 0
    if github_user:
        try:
            commits_30d = fetch_github_commit_count(github_user, days=30)
            logger.info("GitHub commits (30d) for %s: %d", github_user, commits_30d)
        except Exception:
            logger.exception("Failed to fetch GitHub commit count")

    logger.info("Totals — 24h: %.0fs, 7d: %.0fs, 30d: %.0fs, commits: %d",
                s_24h, s_7d, s_30d, commits_30d)

    path = write_badge(s_24h, s_7d, s_30d, commits_30d)
    logger.info("Badge written to %s", path)

    write_stats(sessions, s_30d)


if __name__ == "__main__":
    run()
