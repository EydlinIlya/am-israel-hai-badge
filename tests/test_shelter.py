import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from am_israel_hai_badge.models import Alert, SignalType
from am_israel_hai_badge.shelter import compute_sessions, shelter_seconds_in_window, total_shelter_seconds

TZ = ZoneInfo("Asia/Jerusalem")
AREAS = ["חיפה - מפרץ", "מפרץ חיפה"]


def _alert(minutes: int, signal: SignalType, area: str = "חיפה - מפרץ") -> Alert:
    return Alert(
        timestamp=datetime(2026, 3, 20, 14, minutes, 0, tzinfo=TZ),
        area=area,
        signal_type=signal,
        title="",
    )


class TestComputeSessions(unittest.TestCase):
    def test_simple_session(self):
        alerts = [
            _alert(0, SignalType.PREPARATORY),
            _alert(10, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 600)

    def test_alert_then_safety(self):
        alerts = [
            _alert(0, SignalType.ACTIVE_ALERT),
            _alert(5, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 300)

    def test_preparatory_then_alert_then_safety(self):
        """Additional alerts while in shelter don't reset entry time."""
        alerts = [
            _alert(0, SignalType.PREPARATORY),
            _alert(2, SignalType.ACTIVE_ALERT),
            _alert(10, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 600)
        self.assertEqual(sessions[0].entry_signal, SignalType.PREPARATORY)

    def test_multiple_sessions(self):
        alerts = [
            _alert(0, SignalType.PREPARATORY),
            _alert(10, SignalType.SAFETY),
            _alert(20, SignalType.ACTIVE_ALERT),
            _alert(25, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].duration_seconds, 600)
        self.assertEqual(sessions[1].duration_seconds, 300)

    def test_ongoing_session(self):
        alerts = [
            _alert(0, SignalType.ACTIVE_ALERT),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)
        self.assertIsNone(sessions[0].exit_time)
        self.assertEqual(sessions[0].duration_seconds, 0)

    def test_safety_while_idle_ignored(self):
        alerts = [
            _alert(0, SignalType.SAFETY),
            _alert(5, SignalType.ACTIVE_ALERT),
            _alert(10, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 300)

    def test_filters_by_area(self):
        alerts = [
            Alert(datetime(2026, 3, 20, 14, 0, 0, tzinfo=TZ), "תל אביב", SignalType.ACTIVE_ALERT, ""),
            Alert(datetime(2026, 3, 20, 14, 10, 0, tzinfo=TZ), "תל אביב", SignalType.SAFETY, ""),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 0)

    def test_old_area_name_matched(self):
        """Old area name variant is treated as same location."""
        alerts = [
            _alert(0, SignalType.ACTIVE_ALERT, area="מפרץ חיפה"),
            _alert(10, SignalType.SAFETY, area="חיפה - מפרץ"),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].duration_seconds, 600)

    def test_deduplication(self):
        alerts = [
            _alert(0, SignalType.ACTIVE_ALERT),
            _alert(0, SignalType.ACTIVE_ALERT),  # duplicate
            _alert(10, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(len(sessions), 1)

    def test_no_alerts(self):
        sessions = compute_sessions([], AREAS)
        self.assertEqual(len(sessions), 0)


class TestTotalShelterSeconds(unittest.TestCase):
    def test_sums_completed_sessions(self):
        alerts = [
            _alert(0, SignalType.ACTIVE_ALERT),
            _alert(10, SignalType.SAFETY),
            _alert(20, SignalType.ACTIVE_ALERT),
            _alert(25, SignalType.SAFETY),
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(total_shelter_seconds(sessions), 900)

    def test_ongoing_contributes_zero(self):
        alerts = [
            _alert(0, SignalType.ACTIVE_ALERT),
            _alert(10, SignalType.SAFETY),
            _alert(20, SignalType.ACTIVE_ALERT),
            # no safety — ongoing
        ]
        sessions = compute_sessions(alerts, AREAS)
        self.assertEqual(total_shelter_seconds(sessions), 600)


class TestShelterSecondsInWindow(unittest.TestCase):
    def _sessions(self, *specs):
        """Build sessions from (entry_min, exit_min) tuples."""
        from am_israel_hai_badge.models import ShelterSession
        result = []
        for entry_min, exit_min in specs:
            result.append(ShelterSession(
                entry_time=datetime(2026, 3, 20, 14, entry_min, 0, tzinfo=TZ),
                exit_time=datetime(2026, 3, 20, 14, exit_min, 0, tzinfo=TZ) if exit_min is not None else None,
                entry_signal=SignalType.ACTIVE_ALERT,
                area="חיפה - מפרץ",
            ))
        return result

    def test_fully_inside_window(self):
        sessions = self._sessions((5, 15))
        w_start = datetime(2026, 3, 20, 14, 0, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 30, 0, tzinfo=TZ)
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 600)

    def test_clipped_at_start(self):
        """Session starts before window — only count time inside window."""
        sessions = self._sessions((0, 20))
        w_start = datetime(2026, 3, 20, 14, 10, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 30, 0, tzinfo=TZ)
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 600)

    def test_clipped_at_end(self):
        """Session ends after window — only count time inside window."""
        sessions = self._sessions((5, 25))
        w_start = datetime(2026, 3, 20, 14, 0, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 15, 0, tzinfo=TZ)
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 600)

    def test_clipped_both_sides(self):
        sessions = self._sessions((0, 30))
        w_start = datetime(2026, 3, 20, 14, 10, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 20, 0, tzinfo=TZ)
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 600)

    def test_outside_window(self):
        sessions = self._sessions((0, 5))
        w_start = datetime(2026, 3, 20, 14, 10, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 20, 0, tzinfo=TZ)
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 0)

    def test_ongoing_uses_window_end(self):
        """Ongoing session (no exit) counts up to window_end."""
        sessions = self._sessions((10, None))
        w_start = datetime(2026, 3, 20, 14, 0, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 25, 0, tzinfo=TZ)
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 900)

    def test_multiple_sessions(self):
        sessions = self._sessions((0, 10), (20, 30))
        w_start = datetime(2026, 3, 20, 14, 5, 0, tzinfo=TZ)
        w_end = datetime(2026, 3, 20, 14, 25, 0, tzinfo=TZ)
        # first: clipped to 5-10 = 300s, second: clipped to 20-25 = 300s
        self.assertEqual(shelter_seconds_in_window(sessions, w_start, w_end), 600)


if __name__ == "__main__":
    unittest.main()
