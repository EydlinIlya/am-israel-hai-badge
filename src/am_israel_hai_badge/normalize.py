from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import Alert, SignalType

_TZ = ZoneInfo("Asia/Jerusalem")

# Numeric category codes from official API
_CAT_PREPARATORY = 14
_CAT_SAFETY = 13
# Categories 1-12 are active alerts


def _parse_timestamp(raw: str) -> datetime:
    """Parse timestamp from API format, localized to Asia/Jerusalem."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(raw, fmt)
            return naive.replace(tzinfo=_TZ)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {raw!r}")


def _signal_from_category(cat: int) -> SignalType:
    if cat == _CAT_PREPARATORY:
        return SignalType.PREPARATORY
    if cat == _CAT_SAFETY:
        return SignalType.SAFETY
    if 1 <= cat <= 12:
        return SignalType.ACTIVE_ALERT
    raise ValueError(f"Unknown category: {cat}")


def normalize_alert(raw: dict) -> list[Alert]:
    """Normalize a record from the official oref API into Alert(s).

    Works with both GetAlarmsHistory.aspx (has category + category_desc)
    and AlertsHistory.json (has category only).
    The `data` field may be a single area or comma-separated areas.
    """
    ts = _parse_timestamp(raw["alertDate"])
    signal = _signal_from_category(int(raw["category"]))
    title = raw.get("category_desc", raw.get("title", ""))
    areas = [a.strip() for a in raw.get("data", "").split(",") if a.strip()]
    return [Alert(timestamp=ts, area=area, signal_type=signal, title=title) for area in areas]
