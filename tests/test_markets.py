"""Session-engine tests. The Iran cases encode MEASURED behaviour, including the
case where the user's stated assumption disagrees with the data (Thursday)."""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irx import markets as M


def ts(y, mo, d, h, mi=0, tz="UTC"):
    from zoneinfo import ZoneInfo
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz)).timestamp()


class TestIran(unittest.TestCase):
    def test_saturday_open_at_11_tehran(self):
        # Sat 2026-09-26 11:00 Tehran == 07:30 UTC
        self.assertTrue(M.schedule_open("iran", ts(2026, 9, 26, 7, 30)))
        self.assertFalse(M.schedule_open("iran", ts(2026, 9, 26, 7, 29)))

    def test_weekday_close_1930_tehran(self):
        # Mon 2026-09-28 19:30 Tehran == 16:00 UTC
        self.assertTrue(M.schedule_open("iran", ts(2026, 9, 28, 15, 59)))
        self.assertFalse(M.schedule_open("iran", ts(2026, 9, 28, 16, 0)))

    def test_thursday_is_open_until_1630_not_closed_at_1500(self):
        # measured: Thursday quotes stayed fresh 11:00-16:00 Tehran; the
        # 'closed Thursday afternoon' assumption was wrong for the morning.
        self.assertTrue(M.schedule_open("iran", ts(2026, 10, 1, 11, 30)))   # Thu 15:00 Tehran
        self.assertFalse(M.schedule_open("iran", ts(2026, 10, 1, 13, 1)))   # Thu 16:31 Tehran

    def test_friday_closed(self):
        self.assertFalse(M.schedule_open("iran", ts(2026, 10, 2, 8, 0)))    # Fri 11:30 Tehran


class TestWorld(unittest.TestCase):
    def test_crypto_always(self):
        for t in (ts(2026, 9, 26, 3), ts(2026, 10, 2, 3)):
            self.assertTrue(M.schedule_open("crypto", t))

    def test_us_cash_hours(self):
        # Mon 2026-09-28: 13:30-20:00 UTC (EDT)
        self.assertFalse(M.schedule_open("cash:US", ts(2026, 9, 28, 13, 29)))
        self.assertTrue(M.schedule_open("cash:US", ts(2026, 9, 28, 13, 30)))
        self.assertTrue(M.schedule_open("cash:US", ts(2026, 9, 28, 19, 59)))
        self.assertFalse(M.schedule_open("cash:US", ts(2026, 9, 28, 20, 0)))
        self.assertFalse(M.schedule_open("cash:US", ts(2026, 9, 26, 15, 0)))   # Saturday

    def test_fx_weekly_window(self):
        self.assertTrue(M.schedule_open("fx", ts(2026, 9, 27, 21, 0)))       # Sun 21:00 UTC
        self.assertFalse(M.schedule_open("fx", ts(2026, 9, 27, 20, 59)))
        self.assertFalse(M.schedule_open("fx", ts(2026, 9, 26, 12, 0)))      # Saturday
        self.assertTrue(M.schedule_open("fx", ts(2026, 10, 2, 20, 59)))      # Fri 20:59
        self.assertFalse(M.schedule_open("fx", ts(2026, 10, 2, 21, 0)))

    def test_futures_daily_break(self):
        self.assertFalse(M.schedule_open("futures", ts(2026, 9, 28, 21, 30)))
        self.assertTrue(M.schedule_open("futures", ts(2026, 9, 28, 22, 30)))
        self.assertFalse(M.schedule_open("futures", ts(2026, 9, 27, 21, 0)))  # Sun 21:00, opens 22:00

    def test_next_change_reports_open_and_close(self):
        opened, closed = M.next_change("iran", ts(2026, 10, 2, 8, 0))        # Friday
        self.assertIsNotNone(opened)
        self.assertIsNone(closed)
        # Friday 11:30 Tehran -> next open is Saturday 11:00 Tehran
        import time as _t
        self.assertEqual(_t.strftime("%a %H:%M", _t.gmtime(opened + 3.5 * 3600)), "Sat 11:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
