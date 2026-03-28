from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import ShelterSession, SignalType

_TZ = ZoneInfo("Asia/Jerusalem")
_STATS_PATH = Path(__file__).resolve().parents[2] / "data" / "shelter_stats.md"

_BUCKETS = [
    ("≤5 min  ", 0,        5 * 60),
    ("5–15 min", 5 * 60,  15 * 60),
    ("15–30 m ", 15 * 60, 30 * 60),
    ("30–60 m ", 30 * 60, 60 * 60),
    ("1–2 h   ", 60 * 60, 120 * 60),
    (">2 h    ", 120 * 60, None),
]


def _fmt(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _bar(count: int, max_count: int, width: int = 20) -> str:
    if max_count == 0 or count == 0:
        return ""
    return "█" * max(1, round(count / max_count * width))


def write_stats(sessions: list[ShelterSession], window_seconds: float) -> None:
    """Write shelter statistics markdown to data/shelter_stats.md (gitignored)."""
    completed = [s for s in sessions if s.exit_time is not None]
    n = len(completed)
    today_str = datetime.now(tz=_TZ).strftime("%Y-%m-%d")

    longest = max(completed, key=lambda s: s.duration_seconds, default=None)

    day_counts: Counter[str] = Counter(
        s.entry_time.astimezone(_TZ).strftime("%Y-%m-%d") for s in completed
    )
    busiest = day_counts.most_common(1)[0] if day_counts else ("—", 0)

    bucket_counts = [0] * len(_BUCKETS)
    for s in completed:
        d = s.duration_seconds
        for i, (_label, lo, hi) in enumerate(_BUCKETS):
            if d >= lo and (hi is None or d < hi):
                bucket_counts[i] += 1
                break

    hour_counts = [0] * 24
    for s in completed:
        hour_counts[s.entry_time.astimezone(_TZ).hour] += 1
    max_hour = max(hour_counts, default=1) or 1

    by_signal: Counter[SignalType] = Counter(s.entry_signal for s in completed)

    lines = [
        f"# Shelter Statistics — {today_str}",
        "",
        "## Overview (last 30 days)",
        f"- Sessions: {n} completed",
        f"- Total time: {_fmt(window_seconds)}",
    ]
    if longest:
        d_str = longest.entry_time.astimezone(_TZ).strftime("%Y-%m-%d")
        lines.append(f"- Longest session: {d_str}  {_fmt(longest.duration_seconds)}")
    if busiest[1]:
        lines.append(f"- Busiest day: {busiest[0]}  ({busiest[1]} sessions)")

    max_bucket = max(bucket_counts, default=1) or 1
    lines += ["", "## Duration Distribution"]
    for i, (label, _lo, _hi) in enumerate(_BUCKETS):
        cnt = bucket_counts[i]
        pct = round(cnt / n * 100) if n else 0
        lines.append(f"{label}  {_bar(cnt, max_bucket):<20}  {cnt:>3}  ({pct}%)")

    lines += ["", "## Sessions by Hour of Day"]
    for h in range(24):
        cnt = hour_counts[h]
        lines.append(f"{h:02d}  {_bar(cnt, max_hour, 10):<10}  {cnt}")

    lines += ["", "## Entry Signal"]
    for sig in [SignalType.ACTIVE_ALERT, SignalType.PREPARATORY]:
        cnt = by_signal.get(sig, 0)
        pct = round(cnt / n * 100) if n else 0
        lines.append(f"{sig.value:<20}  {cnt:>3} sessions ({pct}%)")

    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
