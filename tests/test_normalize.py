import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from am_israel_hai_badge.models import SignalType
from am_israel_hai_badge.normalize import normalize_alert

TZ = ZoneInfo("Asia/Jerusalem")


class TestNormalizeAlert(unittest.TestCase):
    def test_active_alert(self):
        raw = {
            "alertDate": "2026-03-20T14:30:00",
            "category": 1,
            "category_desc": "ירי רקטות וטילים",
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].area, "חיפה - מערב")
        self.assertEqual(alerts[0].signal_type, SignalType.ACTIVE_ALERT)
        self.assertEqual(alerts[0].timestamp, datetime(2026, 3, 20, 14, 30, 0, tzinfo=TZ))

    def test_preparatory(self):
        raw = {
            "alertDate": "2026-03-20T14:28:00",
            "category": 14,
            "category_desc": "בדקות הקרובות צפויות להתקבל התרעות באזורך",
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].signal_type, SignalType.PREPARATORY)

    def test_safety(self):
        raw = {
            "alertDate": "2026-03-20T14:45:00",
            "category": 13,
            "category_desc": "האירוע הסתיים",
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].signal_type, SignalType.SAFETY)

    def test_hostile_aircraft(self):
        raw = {
            "alertDate": "2026-03-20T14:30:00",
            "category": 2,
            "category_desc": "חדירת כלי טיס עוין",
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(alerts[0].signal_type, SignalType.ACTIVE_ALERT)

    def test_space_separated_timestamp(self):
        raw = {
            "alertDate": "2026-03-20 14:30:00",
            "category": 1,
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(alerts[0].timestamp, datetime(2026, 3, 20, 14, 30, 0, tzinfo=TZ))

    def test_multiple_areas(self):
        raw = {
            "alertDate": "2026-03-20T14:30:00",
            "category": 1,
            "category_desc": "ירי רקטות וטילים",
            "data": "חיפה - מערב, חיפה - מפרץ, תל אביב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(len(alerts), 3)
        self.assertEqual(alerts[0].area, "חיפה - מערב")
        self.assertEqual(alerts[1].area, "חיפה - מפרץ")
        self.assertEqual(alerts[2].area, "תל אביב")

    def test_string_category(self):
        """Category can come as string from some API responses."""
        raw = {
            "alertDate": "2026-03-20T14:30:00",
            "category": "1",
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(alerts[0].signal_type, SignalType.ACTIVE_ALERT)

    def test_title_from_category_desc(self):
        raw = {
            "alertDate": "2026-03-20T14:30:00",
            "category": 1,
            "category_desc": "ירי רקטות וטילים",
            "data": "חיפה - מערב",
        }
        alerts = normalize_alert(raw)
        self.assertEqual(alerts[0].title, "ירי רקטות וטילים")


if __name__ == "__main__":
    unittest.main()
