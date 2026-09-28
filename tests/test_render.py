"""Render tests: the pages are pure text, so they can be asserted offline.

These pin the curation decisions: grouped blocks, one change per row, closed-market
badge, HTML escaping, and the skip-if-unchanged rule the user asked for.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irx import store as S
from irx.render import pages as P


def ts(y, mo, d, h, mi=0, tz="UTC"):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz)).timestamp()


# Wednesday 2026-09-30 12:00 UTC = 15:30 Tehran -> Iran open, US pre-open
OPEN_TS = ts(2026, 9, 30, 12, 0)
# Friday 2026-10-02 20:00 UTC -> Iran shut, US shut (weekend), only crypto live
CLOSED_TS = ts(2026, 10, 2, 20, 0)


class TestPages(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        now = OPEN_TS
        for sid, v in (("USDIRT", 244_215), ("USDTIRT", 243_688), ("G18", 24_432_100),
                       ("G_ABSHODE", 105_465_000), ("EMAMI", 247_010_000),
                       ("AEDIRT", 66_526), ("CNYIRT", 36_390), ("JPYIRT", 154_376),
                       ("EURIRT", 277_630), ("NASDAQ", 30_321.74), ("SP500", 7_711.23),
                       ("WTI", 92.88), ("BRENT", 98.93), ("XAUUSD", 4135.72),
                       ("XAGUSD", 61.31), ("DXY", 101.18), ("EURUSD", 1.1365),
                       ("USDJPY", 157.23), ("USDCNY", 6.6688), ("USDAED", 3.673),
                       ("BTCUSD", 83_276), ("BTCIRT", 20_324_797_401)):
            S.record(self.c, sid, v, ts=now, source="test")
            # a previous-day close, so the change column is computed rather than "—"
            S.record(self.c, sid, v * 1.01, ts=now - 86400, source="test")

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    def test_prices_page_has_groups_and_values(self):
        t = P.prices(self.c, OPEN_TS)
        for frag in ("IRX", "Indices", "FX &amp; dollar", "Energy &amp; metals",
                     "Tehran street", "244,215", "30,321.7"):
            self.assertIn(frag, t)
        self.assertIn("<pre>", t)          # monospace is what makes the columns align
        self.assertIn("</pre>", t)

    def test_ampersand_is_escaped_for_telegram_html(self):
        t = P.prices(self.c, OPEN_TS)
        self.assertIn("S&amp;P 500", t)
        self.assertNotIn("S&P 500", t)

    def test_closed_market_gets_a_badge_and_a_note(self):
        t = P.prices(self.c, CLOSED_TS)
        self.assertIn("⏸", t)
        self.assertIn("last close", t)     # Iran shut on Friday

    def test_crypto_is_never_badged_closed(self):
        t = P.prices(self.c, CLOSED_TS)
        btc = [ln for ln in t.splitlines() if "Bitcoin" in ln][0]
        self.assertNotIn("⏸", btc)         # 24/7 market

    def test_rows_carry_exactly_one_change_column(self):
        t = P.prices(self.c, OPEN_TS)
        row = [ln for ln in t.splitlines() if "EUR/USD" in ln][0]
        self.assertEqual(row.count("%"), 1)

    def test_every_rendered_label_fits_the_column(self):
        """A label longer than the column is silently cut mid-word in production
        (seen live: 'USDT (digital' / 'Melted gold ('), so fail here instead."""
        from irx import config as C
        for spec in C.SERIES:
            if spec.get("render", True):
                self.assertLessEqual(len(spec["label"]), P.LBL_W,
                                     "%s label too long: %r" % (spec["id"], spec["label"]))

    def test_label_escaping_happens_before_padding(self):
        # the padding must count the RAW length, so the value column stays aligned
        # once Telegram renders "&amp;" back to "&"
        import html as _html
        t = P.prices(self.c, OPEN_TS)
        pre = t.split("<pre>")[1].split("</pre>")[0]
        rows = {ln[:12]: _html.unescape(ln) for ln in pre.splitlines()}
        sp = [v for k, v in rows.items() if k.startswith("S&")][0]
        nas = [v for k, v in rows.items() if k.startswith("NASDAQ")][0]
        self.assertIn("S&P 500", sp)
        # numbers are RIGHT-aligned, so the values must share an END column (and
        # therefore the change column must start at the same offset in both rows)
        self.assertEqual(sp.index("7,711.2") + len("7,711.2"),
                         nas.index("30,321.7") + len("30,321.7"))

    def test_missing_series_renders_a_dash_not_a_crash(self):
        t = P.render(self.c, P.PAGE_PRICES, OPEN_TS)
        for ln in t.splitlines():
            self.assertNotIn("None", ln)

    def test_signature_changes_when_a_value_changes(self):
        sig1 = P.signature(self.c, P.PAGE_PRICES, OPEN_TS)
        S.record(self.c, "WTI", 99.99, ts=OPEN_TS, source="test")
        sig2 = P.signature(self.c, P.PAGE_PRICES, OPEN_TS)
        self.assertNotEqual(sig1, sig2)

    def test_all_closed_detects_the_quiet_hours(self):
        self.assertFalse(P.all_closed(self.c, P.PAGE_PRICES, OPEN_TS))
        # BTC is 24/7 and on the page, so even Friday night is not "all closed"
        self.assertFalse(P.all_closed(self.c, P.PAGE_PRICES, CLOSED_TS))

    def test_cross_page_states_its_tiers(self):
        t = P.cross_page(self.c, OPEN_TS)
        self.assertIn("Tier A", t)
        self.assertIn("Tier B", t)
        self.assertIn("consensus", t)
        self.assertIn("dispersion", t)

    def test_trend_page_says_so_when_history_is_thin(self):
        t = P.trend_page(self.c, OPEN_TS)
        self.assertIn("minimum", t)

    def test_all_pages_render_without_error(self):
        for page in P.PAGE_ORDER:
            self.assertTrue(P.render(self.c, page, OPEN_TS).strip())

    def test_render_is_length_capped_for_telegram(self):
        self.assertLessEqual(len(P.render(self.c, P.PAGE_OHLC, OPEN_TS)), 4000)

    def test_unknown_page_falls_back_to_prices(self):
        self.assertEqual(P.render(self.c, "nope", OPEN_TS),
                         P.render(self.c, P.PAGE_PRICES, OPEN_TS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
