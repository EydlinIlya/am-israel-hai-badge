from __future__ import annotations

from datetime import datetime

from .models import Alert, ShelterSession, SignalType


def compute_sessions(alerts: list[Alert], area_names: list[str]) -> list[ShelterSession]:
    """Run shelter state machine over sorted alerts for configured areas.

    Filters alerts to only those matching area_names (exact match).
    All matching area names are treated as the same location (single state machine).
    Returns completed + ongoing sessions.
    """
    names_set = set(area_names)
    relevant = [a for a in alerts if a.area in names_set]

    # Deduplicate by (timestamp, area, signal_type)
    seen: set[tuple] = set()
    deduped: list[Alert] = []
    for a in relevant:
        key = (a.timestamp, a.area, a.signal_type)
        if key not in seen:
            seen.add(key)
            deduped.append(a)

    # Sort by timestamp
    deduped.sort(key=lambda a: a.timestamp)

    sessions: list[ShelterSession] = []
    entry_time = None
    entry_signal = None
    entry_area = None

    for alert in deduped:
        if entry_time is None:
            # IDLE state
            if alert.signal_type in (SignalType.PREPARATORY, SignalType.ACTIVE_ALERT):
                entry_time = alert.timestamp
                entry_signal = alert.signal_type
                entry_area = alert.area
        else:
            # IN_SHELTER state
            if alert.signal_type == SignalType.SAFETY:
                sessions.append(ShelterSession(
                    entry_time=entry_time,
                    exit_time=alert.timestamp,
                    entry_signal=entry_signal,
                    area=entry_area,
                ))
                entry_time = None
                entry_signal = None
                entry_area = None

    # Ongoing session (no safety signal yet)
    if entry_time is not None:
        sessions.append(ShelterSession(
            entry_time=entry_time,
            exit_time=None,
            entry_signal=entry_signal,
            area=entry_area,
        ))

    return sessions


def total_shelter_seconds(sessions: list[ShelterSession]) -> float:
    """Sum duration of all completed sessions (ongoing sessions contribute 0)."""
    return sum(s.duration_seconds for s in sessions)


def shelter_seconds_in_window(
    sessions: list[ShelterSession],
    window_start: datetime,
    window_end: datetime,
) -> float:
    """Sum shelter seconds, clipping sessions to a time window.

    Sessions that overlap the window edges are clipped (not dropped).
    Ongoing sessions (no exit_time) use window_end as a stand-in exit.
    """
    total = 0.0
    for s in sessions:
        exit_time = s.exit_time if s.exit_time is not None else window_end
        # Clip to window
        start = max(s.entry_time, window_start)
        end = min(exit_time, window_end)
        if end > start:
            total += (end - start).total_seconds()
    return total
