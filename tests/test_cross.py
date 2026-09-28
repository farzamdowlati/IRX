"""Cross-market analysis tests: parity maths (including the per-100 yen trap),
the triangulation, and the guards that keep thin samples from being reported."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irx import store as S
from irx.analysis import cross
from irx.ingest import record_gaps


class TestParity(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        self.now = 1_800_000_000

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    def _put(self, sid, val, dp=None, ts=None):
        S.record(self.c, sid, val, ts=ts or self.now, source="test")

    def test_cny_gap_sign_and_size(self):
        # street USD 244,000; Tehran yuan 36,000 T; world 6.67 CNY/USD
        self._put("USDIRT", 244_000)
        self._put("CNYIRT", 36_000)
        self._put("USDCNY", 6.67)
        g = [x for x in cross.parity_gaps(self.c) if x["label"] == "Yuan"][0]
        # implied 36,000 * 6.67 = 240,120 -> a NEGATIVE gap vs 244,000
        self.assertLess(g["gap_pct"], 0)
        self.assertAlmostEqual(g["implied_usdirt"], 240_120, delta=1)
        self.assertAlmostEqual(g["gap_pct"], -1.59, delta=0.05)

    def test_yen_is_quoted_per_100_and_must_be_divided(self):
        self._put("USDIRT", 244_000)
        self._put("JPYIRT", 154_000)      # Toman per 100 yen
        self._put("USDJPY", 157.0)
        g = [x for x in cross.parity_gaps(self.c) if x["label"].startswith("Yen")][0]
        # (154,000 / 100) * 157 = 241,780 -- forgetting the /100 gives 100x off
        self.assertAlmostEqual(g["implied_usdirt"], 241_780, delta=1)
        self.assertGreater(g["implied_usdirt"], 200_000)

    def test_eur_implied_uses_local_over_world_rate(self):
        self._put("USDIRT", 244_000)
        self._put("EURIRT", 277_000)      # Toman per EUR
        self._put("EURUSD", 1.1365)
        g = [x for x in cross.parity_gaps(self.c) if x["label"] == "Euro"][0]
        self.assertAlmostEqual(g["implied_usdirt"], 277_000 / 1.1365, delta=20)

    def test_gold_parity_and_coin_premium(self):
        self._put("XAUUSD", 4135.72)
        self._put("USDIRT", 244_000)
        self._put("G18", 24_432_100)
        self._put("EMAMI", 247_010_000)
        gp = cross.gold_parity(self.c)
        self.assertIsNotNone(gp)
        self.assertAlmostEqual(gp["tehran_pure_gram"], 24_432_100 / 0.75, delta=1)
        # coin melt = pure gram * 8.105 g * 0.900
        self.assertAlmostEqual(gp["coin_melt_tmn"],
                               (24_432_100 / 0.75) * 8.105 * 0.900, delta=50)
        self.assertGreater(gp["coin_premium_pct"], 0)      # Emami trades over melt

    def test_tether_premium_sign(self):
        self._put("USDIRT", 244_000)
        self._put("USDTIRT", 243_000)
        self.assertAlmostEqual(cross.tether_premium(self.c), -0.41, delta=0.02)


class TestTriangulation(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        self.now = 1_800_000_000

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    def _put(self, sid, val):
        S.record(self.c, sid, val, ts=self.now, source="test")

    def test_agreeing_legs_give_low_dispersion(self):
        self._put("USDIRT", 244_000)
        self._put("USDTIRT", 243_900)
        self._put("G18", 24_432_100)        # -> gold-implied ~244,995
        self._put("XAUUSD", 4135.72)
        self._put("BTCIRT", 20_324_797_401)
        self._put("BTCUSD", 83_416.05)
        tri = cross.triangulation(self.c)
        # 4 instruments: street USD, USDT, gold, BTC (one leg per instrument)
        self.assertEqual(len(tri["legs"]), 4)
        vals = [l["value"] for l in tri["legs"]]
        self.assertLessEqual(min(vals), tri["consensus"])
        self.assertLessEqual(tri["consensus"], max(vals))
        self.assertLess(tri["dispersion_mad_pct"], 1.0)
        self.assertLess(tri["spread_pct"], 2.0)

    def test_usdt_venues_are_not_double_counted(self):
        self._put("USDIRT", 244_000)
        self._put("USDTIRT", 243_900)
        S.record(self.c, "USDTIRT@wallex", 244_091, ts=self.now, source="test")
        tri = cross.triangulation(self.c)
        self.assertEqual(len([l for l in tri["legs"] if "USDT" in l["name"]]), 1)

    def test_dislocated_leg_is_named_by_deviation_and_spread(self):
        self._put("USDIRT", 244_000)
        self._put("USDTIRT", 244_100)
        self._put("G18", 24_432_100)
        self._put("XAUUSD", 4135.72)
        self._put("BTCIRT", 24_400_000_000)   # BTC leg ~20% above the others
        self._put("BTCUSD", 83_416.05)
        tri = cross.triangulation(self.c)
        worst = max(tri["legs"], key=lambda l: abs(l["dev_pct"]))
        self.assertIn("BTC", worst["name"])
        # MAD is robust BY DESIGN, so one outlier moves spread far more than MAD;
        # the outlier is caught by spread_pct + the leg's own dev_pct.
        self.assertGreater(tri["spread_pct"], 10.0)
        self.assertGreater(abs(worst["dev_pct"]), 10.0)

    def test_returns_none_with_a_single_leg(self):
        self._put("USDIRT", 244_000)
        self.assertIsNone(cross.triangulation(self.c))


class TestGuards(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        self.now = 1_800_000_000

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    def test_robust_z_needs_a_minimum_sample(self):
        self.assertIsNone(cross.robust_z([1.0, 2.0, 3.0]))
        vals = [10.0, 10.4, 9.7, 10.2, 11.0, 9.9, 10.1, 10.3, 9.8, 10.6, 10.2, 9.6, 15.0]
        z = cross.robust_z(vals)
        self.assertIsNotNone(z)
        self.assertGreater(z, 3.0)         # the last point really is an outlier

    def test_robust_z_refuses_a_degenerate_distribution(self):
        # >50% identical values -> MAD = 0 -> the z-score is undefined, not infinite
        self.assertIsNone(cross.robust_z([1.0] * 20 + [9.0]))

    def test_half_life_needs_a_minimum_sample(self):
        for i in range(5):
            S.record(self.c, "gap:tether", 1.0, ts=self.now + i * 900, source="t")
        self.assertIsNone(cross.half_life(self.c, "gap:tether", self.now + 3600))

    def test_half_life_recovers_a_known_decay(self):
        # gap halves every step: b = 0.5 -> half-life = exactly one step
        v = 100.0
        for i in range(60):
            S.record(self.c, "gap:test", v, ts=self.now + i * 900, source="t")
            v /= 2.0
        hl = cross.half_life(self.c, "gap:test", self.now + 3600 * 24, days=30,
                             session_only=False)
        self.assertIsNotNone(hl)
        self.assertAlmostEqual(hl["half_life_h"], 0.25, delta=0.05)   # 900 s step

    def test_rolling_corr_needs_paired_history(self):
        self.assertIsNone(cross.rolling_corr(self.c, "USDIRT", "WTI", self.now))

    def test_record_gaps_persists_the_derived_series(self):
        S.record(self.c, "USDIRT", 244_000, ts=self.now, source="t")
        S.record(self.c, "CNYIRT", 36_000, ts=self.now, source="t")
        S.record(self.c, "USDCNY", 6.67, ts=self.now, source="t")
        S.record(self.c, "USDTIRT", 243_900, ts=self.now, source="t")
        n = record_gaps(self.c, now=self.now)
        self.assertGreaterEqual(n, 3)
        self.assertIsNotNone(S.latest(self.c, "gap:Yuan"))
        self.assertIsNotNone(S.latest(self.c, "gap:tether"))
        self.assertIsNotNone(S.latest(self.c, "tri:consensus"))
        self.assertIsNotNone(S.latest(self.c, "tri:dispersion"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
