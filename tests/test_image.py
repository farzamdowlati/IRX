"""Image-table tests. Skipped cleanly where Pillow or a usable font is missing —
the point of the fallback design is that a bare host degrades to text, not crashes.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from irx import store as S
from irx.render import imagetable

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except Exception:                                            # noqa: BLE001
    HAVE_PIL = False

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()


@unittest.skipUnless(HAVE_PIL, "Pillow not installed on this interpreter")
class TestImageTable(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        for sid, v in (("NASDAQ", 30_321.74), ("SP500", 7_711.23), ("WTI", 92.88),
                       ("XAUUSD", 4135.72), ("USDIRT", 244_215), ("G18", 24_432_100),
                       ("BTCUSD", 83_276)):
            S.record(self.c, sid, v, ts=NOW, source="test")
            S.put_bar(self.c, sid, "5m", NOW - 600, o=v * 0.99, h=v * 1.01,
                      l=v * 0.98, c=v, n=5, src="test")
        self.c.commit()

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    def test_available_reports_a_usable_font(self):
        self.assertTrue(imagetable.available())

    def test_renders_a_real_png(self):
        png = imagetable.render_ohlc(self.c, NOW)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"), "not a PNG")
        self.assertGreater(len(png), 3000)

    def test_image_fits_a_phone_width_and_is_not_absurdly_tall(self):
        from PIL import Image
        import io
        png = imagetable.render_ohlc(self.c, NOW)
        img = Image.open(io.BytesIO(png))
        self.assertLessEqual(img.width, 1200)
        self.assertGreater(img.width, 600)
        self.assertLess(img.height, 3000)

    def test_no_column_overlaps_another(self):
        """Regression for a defect vision caught: the 'open' column's right edge sat
        where long labels end, so values printed on top of the index names. Uses the
        renderer's own geometry and real glyph extents, not guessed character counts."""
        from irx import config as C
        from irx.analysis import ohlc
        f_row = imagetable.font(imagetable.F_ROW)
        g = imagetable.geometry()
        worst = 0.0
        for spec in C.SERIES:
            if not spec.get("render", True):
                continue
            s = ohlc.summary(self.c, spec["id"], NOW)
            if not s:
                continue
            label_px = g["label_x"] + f_row.getlength(
                imagetable._fit(spec["label"], f_row, imagetable.LABEL_W - 10))
            worst = max(worst, label_px)
            val = imagetable._fmt_num(s["open"], spec.get("dp", 2), f_row,
                                      g["span"] - 10)
            val_left = g["cols"][0] - f_row.getlength(val)
            self.assertGreaterEqual(
                val_left, label_px - 1,
                "%s: value %r starts at %.0f, label ends at %.0f"
                % (spec["id"], val, val_left, label_px))
        self.assertLess(worst, g["num_x0"], "labels must stay inside their column")

    def test_wide_values_are_abbreviated_to_fit(self):
        """The user's rule: K for thousand, M for million, no dozens of zeroes —
        but exact digits while they still carry information."""
        f_row = imagetable.font(imagetable.F_ROW)
        g = imagetable.geometry()
        budget = g["span"] - 10
        self.assertEqual(imagetable._fmt_num(92.88, 2, f_row, budget), "92.88")
        self.assertEqual(imagetable._fmt_num(1.1365, 4, f_row, budget), "1.1365")
        self.assertEqual(imagetable._fmt_num(30_321.74, 1, f_row, budget), "30,321.7")
        self.assertEqual(imagetable._fmt_num(65_448.0, 1, f_row, budget), "65,448.0")
        self.assertEqual(imagetable._fmt_num(244_215, 0, f_row, budget), "244.2K")
        self.assertEqual(imagetable._fmt_num(105_653_000, 0, f_row, budget), "105.65M")
        for v, dp in ((244_215, 0), (105_653_000, 0), (247_005_000, 0), (8_647.3, 1)):
            self.assertLessEqual(f_row.getlength(imagetable._fmt_num(v, dp, f_row, budget)),
                                 budget)

    def test_numeric_columns_have_a_gutter(self):
        """Four numeric columns with no gutter read as one run of digits."""
        from irx import config as C
        from irx.analysis import ohlc
        f_row = imagetable.font(imagetable.F_ROW)
        g = imagetable.geometry()
        self.assertEqual(len(g["dividers"]), 4)          # between the 5 numeric cols
        self.assertEqual(len(g["cols"]), 5)              # O H L C + range
        for spec in C.SERIES:
            if not spec.get("render", True):
                continue
            s = ohlc.summary(self.c, spec["id"], NOW)
            if not s:
                continue
            widest = max(f_row.getlength(imagetable._fmt_num(s[k], spec.get("dp", 2)))
                         for k in ("open", "high", "low", "close"))
            self.assertLess(widest, g["span"] - 8,
                            "%s: a value is wider than its column" % spec["id"])

    def test_long_labels_are_ellipsised_not_clipped(self):
        f = imagetable.font(imagetable.F_ROW)
        out = imagetable._fit("A very long instrument name that cannot fit", f, 100)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(f.getlength(out), 100)

    def test_returns_none_with_nothing_to_draw(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        empty = S.connect(path)
        try:
            self.assertIsNone(imagetable.render_ohlc(empty, NOW))
        finally:
            empty.close()
            os.unlink(path)

    def test_caption_is_within_the_telegram_media_limit(self):
        from irx.render import pages as P
        cap = P.ohlc_caption(self.c, NOW)
        self.assertLessEqual(len(cap), 1024)
        self.assertIn("OHLC", cap)

    def test_narrow_text_fallback_keeps_rows_phone_sized(self):
        """The fallback exists for hosts without Pillow, so every one of its lines must
        fit a phone — that was the original defect."""
        import re
        from irx.render import pages as P
        plain = re.sub(r"<[^>]+>", "", P.ohlc_page(self.c, NOW))
        lines = [ln for ln in plain.splitlines()
                 if ln.startswith("  O ") or ln.startswith("  L ")]
        self.assertTrue(lines)
        for ln in lines:
            self.assertLessEqual(len(ln), 30, "line too wide for a phone: %r" % ln)
        labels = [ln for ln in plain.splitlines() if ln.strip() and
                  not ln.startswith("  ") and "OHLC" not in ln and "◆" not in ln]
        for ln in labels:
            self.assertLessEqual(len(ln), 20, "label line too wide: %r" % ln)


if __name__ == "__main__":
    unittest.main(verbosity=2)
