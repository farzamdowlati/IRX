"""Store tests: idempotency, ranges, chat state, alerts, bars, retention."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irx import store as S


class TestStore(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    def test_record_and_latest(self):
        now = int(time.time())
        self.assertTrue(S.record(self.c, "USDIRT", 244000, ts=now, src_ts=now - 30,
                                 source="brsapi"))
        r = S.latest(self.c, "USDIRT")
        self.assertEqual(r["value"], 244000)
        self.assertEqual(r["source"], "brsapi")

    def test_same_ts_updates_not_duplicates(self):
        t = 1_700_000_000
        S.record(self.c, "USDIRT", 100, ts=t, source="a")
        S.record(self.c, "USDIRT", 200, ts=t, source="b")
        rows = S.window(self.c, "USDIRT", t - 1, t + 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 200)

    def test_none_value_is_not_stored(self):
        self.assertFalse(S.record(self.c, "USDIRT", None, ts=123))
        self.assertIsNone(S.latest(self.c, "USDIRT"))

    def test_at_or_before_and_window(self):
        for i, v in enumerate([10, 20, 30]):
            S.record(self.c, "X", v, ts=1000 + i * 60)
        self.assertEqual(S.at_or_before(self.c, "X", 1060)["value"], 20)
        self.assertEqual([r["value"] for r in S.window(self.c, "X", 1000)], [10, 20, 30])

    def test_latest_all_and_stats(self):
        S.record(self.c, "A", 1, ts=100)
        S.record(self.c, "A", 5, ts=200)
        S.record(self.c, "B", 9, ts=150)
        la = S.latest_all(self.c)
        self.assertEqual(la["A"]["value"], 5)
        self.assertEqual(la["B"]["value"], 9)
        st = S.stats(self.c, "A", 0)
        self.assertEqual((st["n"], st["lo"], st["hi"]), (2, 1, 5))

    def test_chat_state_roundtrip(self):
        S.upsert_chat(self.c, 42)
        S.set_chat(self.c, 42, msg_id=7, page="cross", state_json='{"h":"abc"}')
        ch = S.get_chat(self.c, 42)
        self.assertEqual((ch["msg_id"], ch["page"]), (7, "cross"))
        S.set_chat(self.c, 42, active=0)
        self.assertEqual(S.chats(self.c), [])
        self.assertEqual(len(S.chats(self.c, active_only=False)), 1)

    def test_alert_cooldown_lookup(self):
        now = int(time.time())
        S.add_alert(self.c, "move", "WTI", "WTI +3%", ts=now - 30)
        self.assertIsNotNone(S.recent_alert(self.c, "move", "WTI", now - 3600))
        self.assertIsNone(S.recent_alert(self.c, "move", "WTI", now - 10))
        self.assertIsNone(S.recent_alert(self.c, "move", "SP500", now - 3600))
        self.assertEqual(len(S.unsent_alerts(self.c)), 1)

    def test_bars_upsert_and_order(self):
        for i in range(3):
            S.put_bar(self.c, "WTI", "5m", 1000 + i * 300, o=1, h=2, l=0.5, c=1.5, n=i)
        S.put_bar(self.c, "WTI", "5m", 1000, o=9, h=9, l=9, c=9, n=99)
        bars = S.get_bars(self.c, "WTI", "5m")
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0]["o"], 9)              # upsert won
        self.assertEqual(bars[-1]["open_ts"], 1600)    # ascending

    def test_failure_counters(self):
        self.assertEqual(S.bump_failures(self.c, "WTI"), 1)
        self.assertEqual(S.bump_failures(self.c, "WTI"), 2)
        S.clear_failures(self.c, "WTI")
        self.assertEqual(S.failure_count(self.c, "WTI"), 0)

    def test_prune_keeps_recent(self):
        now = int(time.time())
        S.record(self.c, "WTI", 1, ts=now - 400 * 86400)      # ancient
        S.record(self.c, "WTI", 2, ts=now - 60)
        S.record(self.c, "USDIRT", 3, ts=now - 200 * 86400)   # iran retention is 400d
        out = S.prune(self.c, now)
        self.assertEqual(out["sample_intl"], 1)
        self.assertEqual(out["sample_iran"], 0)
        self.assertEqual(len(S.window(self.c, "WTI", 0)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
