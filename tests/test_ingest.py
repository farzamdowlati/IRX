"""Ingest tests — no network: providers are stubbed, so the cycle logic, grid
alignment, staleness, failure counters and cross-check rows are what is tested."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irx import config as C, ingest, store as S
from irx.sources import others


class FakeBrs:
    """Stand-in for sources.brsapi (bulk payload + archive)."""
    payload = {}
    archived = 0

    @classmethod
    def snapshot(cls, force=False):
        return cls.payload

    @classmethod
    def archive_raw(cls):
        cls.archived += 1
        return "/tmp/fake.json"


def ok_quote(symbol):
    return {"value": 100.0 + len(symbol), "src_ts": 1_800_000_000, "source": "stub:%s" % symbol,
            "stale": False, "extra": {}}


def boom(symbol):
    raise RuntimeError("provider down")


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        self._prov = dict(others.PROVIDERS)
        self._brs = ingest.brsapi
        self._cross = C.FETCH_CONCURRENCY

    def tearDown(self):
        others.PROVIDERS.clear()
        others.PROVIDERS.update(self._prov)
        ingest.brsapi = self._brs
        C.FETCH_CONCURRENCY = self._cross
        self.c.close()
        os.unlink(self.path)

    def test_grid_alignment(self):
        # assert the property, not hand-computed constants: the grid point must be
        # the largest multiple of the step at or below `now`.
        for step_min in (5, 15):
            for now in (1_800_000_123, 1_800_000_000, 1_799_999_999):
                g = ingest._grid(now, step_min)
                self.assertEqual(g % (step_min * 60), 0)
                self.assertLessEqual(g, now)
                self.assertLess(now - g, step_min * 60)

    def test_intl_cycle_writes_all_series_and_crosschecks(self):
        others.load_providers()
        for k in list(others.PROVIDERS):
            others.PROVIDERS[k] = ok_quote
        out = ingest.run_intl(self.c, now=1_800_000_123.0)
        self.assertEqual(out["fail"], [])
        self.assertEqual(out["ok"], len(C.INTL_IDS))
        # canonical row for a two-source series plus its cross-check row
        self.assertIsNotNone(S.latest(self.c, "XAUUSD"))
        self.assertIsNotNone(S.latest(self.c, "XAUUSD@swissquote"))
        spec = C.BY_ID["XAUUSD"]
        self.assertEqual(S.latest(self.c, "XAUUSD")["source"],
                         "stub:%s" % spec["sources"][0][1])

    def test_failed_series_records_no_value_and_counts_failures(self):
        others.load_providers()
        for k in list(others.PROVIDERS):
            others.PROVIDERS[k] = boom
        out = ingest.run_intl(self.c, now=1_800_000_000.0)
        self.assertEqual(out["ok"], 0)
        self.assertEqual(len(out["fail"]), len(C.INTL_IDS))
        self.assertIsNone(S.latest(self.c, "WTI"))
        self.assertEqual(S.failure_count(self.c, "WTI"), 1)
        ingest.run_intl(self.c, now=1_800_000_300.0)
        self.assertEqual(S.failure_count(self.c, "WTI"), 2)

    def test_fallback_chain_uses_second_provider(self):
        others.load_providers()
        for k in list(others.PROVIDERS):
            others.PROVIDERS[k] = boom
        others.PROVIDERS["swissquote"] = ok_quote
        out = ingest.fetch_series(C.BY_ID["XAUUSD"], 1_800_000_000.0)
        self.assertEqual(out["used"], "swissquote")
        self.assertTrue(out["errors"])

    def test_stale_src_ts_marks_stale(self):
        others.load_providers()
        for k in list(others.PROVIDERS):
            others.PROVIDERS[k] = lambda s: {"value": 5, "src_ts": 1_000,
                                             "source": "old", "stale": False, "extra": {}}
        ingest.run_intl(self.c, now=1_800_000_000.0)
        self.assertEqual(S.latest(self.c, "WTI")["stale"], 1)

    def test_iran_cycle_writes_every_iran_series_from_one_payload(self):
        ingest.brsapi = FakeBrs
        FakeBrs.payload = {"USD": {"price": 244000, "t": 1_800_000_000},
                           "USDT_IRT": {"price": 243900, "t": 1_800_000_000},
                           "IR_GOLD_18K": {"price": 24000000, "t": 1_800_000_000},
                           "IR_GOLD_MELTED": {"price": 105000000, "t": 1_800_000_000},
                           "IR_COIN_EMAMI": {"price": 246000000, "t": 1_800_000_000},
                           "AED": {"price": 66000, "t": 1_800_000_000},
                           "CNY": {"price": 36000, "t": 1_800_000_000},
                           "JPY": {"price": 154000, "t": 1_800_000_000},
                           "EUR": {"price": 277000, "t": 1_800_000_000}}
        out = ingest.run_iran(self.c, now=1_800_000_100.0)
        self.assertEqual(out["fail"], [])
        self.assertEqual(out["ok"], len(C.IRAN_CADENCE_IDS))
        self.assertEqual(S.latest(self.c, "USDIRT")["value"], 244000)
        self.assertEqual(FakeBrs.archived, 1)

    def test_iran_frozen_src_time_flags_closed_market(self):
        ingest.brsapi = FakeBrs
        # src_time is 20 minutes old -> the feed is frozen -> market shut
        FakeBrs.payload = {"USD": {"price": 244000, "t": 1_799_998_900}}
        out = ingest.run_iran(self.c, now=1_800_000_100.0)
        self.assertTrue(out["src_frozen"])
        self.assertEqual(S.latest(self.c, "USDIRT")["stale"], 1)

    def test_iran_bulk_failure_marks_every_series(self):
        class Dead:
            @staticmethod
            def snapshot(force=False):
                raise RuntimeError("brsapi unreachable")

        ingest.brsapi = Dead
        out = ingest.run_iran(self.c, now=1_800_000_000.0)
        self.assertEqual(out["ok"], 0)
        self.assertEqual(S.failure_count(self.c, "USDIRT"), 1)

    def test_backfill_writes_bars_from_worker_threads(self):
        """Regression: workers must not touch the sqlite connection. Fetching is
        parallel, writing is single-threaded."""
        class FakeBq:
            @staticmethod
            def ohlc(symbol, interval, count, timeout=40):
                return [(1_700_000_000 + i * 300, 10.0, 11.0, 9.0, 10.5, i) for i in range(3)]

        real = ingest.biquote
        ingest.biquote = FakeBq
        try:
            out = ingest.backfill_bars(self.c, intervals=("5m", "1h"), count=3)
        finally:
            ingest.biquote = real
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["bars"], 0)
        self.assertEqual(len(S.get_bars(self.c, C.BY_ID["WTI"]["id"], "5m")), 3)

    def test_backfill_reports_provider_errors_without_crashing(self):
        class BadBq:
            @staticmethod
            def ohlc(symbol, interval, count, timeout=40):
                raise RuntimeError("ohlc down")

        real = ingest.biquote
        ingest.biquote = BadBq
        try:
            out = ingest.backfill_bars(self.c, intervals=("5m",), count=3)
        finally:
            ingest.biquote = real
        self.assertEqual(out["bars"], 0)
        self.assertTrue(out["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
