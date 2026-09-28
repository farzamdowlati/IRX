"""irx/render/imagetable.py — PNG tables for pages a phone cannot read as text.

Why this exists: a Telegram <pre> table stays a table only while every row fits the
phone's monospace width (~34 chars at the default font). The Prices page fits; OHLC
needs six columns, so its rows wrapped and the columns collapsed into noise — the
user's words: "does not look like a table at all". Shrinking the data further would
cost real information, so this renders a genuine table to PNG with Pillow: no
browser, no headless Chrome, one small dependency, fully deterministic output.

Fonts: DejaVu ships with most Linux distros (present on tr under
/usr/share/fonts/truetype/dejavu/). If no usable face is found, `available()` reports
False and the caller falls back to a narrow text layout — a bare host degrades
instead of crashing.
"""
from __future__ import annotations

import io
import os
import time

from .. import config as C, markets as M, store as S
from ..analysis import ohlc

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

BG = (22, 23, 26)
BAND = (35, 37, 42)
ALT = (28, 30, 34)
TEXT = (232, 232, 234)
MUTED = (154, 160, 166)
ACCENT = (88, 166, 255)
GREEN = (63, 185, 80)
RED = (248, 81, 73)

W = 1000
PAD = 26
F_ROW, F_HEAD, F_TITLE = 25, 23, 34
ROW_H = 54
LABEL_W = 215          # reserved for the label column; numbers may not enter it
GRID = (47, 50, 56)    # faint column dividers: without them four numeric columns
                       # read as one run of digits (vision-verified complaint)

_font_cache: dict = {}


def _load(paths, size):
    from PIL import ImageFont
    key = (tuple(paths), size)
    if key in _font_cache:
        return _font_cache[key]
    for p in paths:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _font_cache[key] = f
                return f
            except OSError:
                continue
    _font_cache[key] = None
    return None


def font(size: int, bold: bool = False):
    return _load(BOLD_CANDIDATES if bold else FONT_CANDIDATES, size)


def available() -> bool:
    try:
        import PIL  # noqa: F401
    except Exception:                                        # noqa: BLE001
        return False
    return font(F_ROW) is not None


def _num(v, dp: int) -> str:
    if v is None:
        return "—"
    return "{:,.{}f}".format(v, dp)


def _fmt_num(v, dp: int, fnt=None, max_px: float | None = None) -> str:
    """Exact digits while they carry information, K/M once they stop.

    User's rule (2026-09-28): "You may use K for 1000 and M for Million. No need to
    include dozens of zeroes." So 30,321.7 stays exact (six characters of real
    information) while 105,653,000 becomes 105.65M — the trailing zeros carry nothing.
    The width guard only kicks in for a value that still cannot fit its column.
    """
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e6:
        s = "%.2fM" % (v / 1e6)
    elif a >= 1e5:
        s = "%.1fK" % (v / 1e3)
    else:
        s = "{:,.{}f}".format(v, dp)
    if fnt is not None and max_px is not None and fnt.getlength(s) > max_px:
        s = ("%.1fM" % (v / 1e6)) if a >= 1e6 else ("%.0fK" % (v / 1e3)) \
            if a >= 1e5 else ("%.0f" % v)
    return s


def _fit(text: str, fnt, max_px: float) -> str:
    """Truncate to fit a pixel budget. Guessing a character count is what produced
    overlapping columns; measure instead."""
    if fnt.getlength(text) <= max_px:
        return text
    t = text
    while t and fnt.getlength(t + "…") > max_px:
        t = t[:-1]
    return (t + "…") if t else ""


def geometry() -> dict:
    """Column geometry in one place: the renderer and the overlap test must not
    derive it independently, or the test passes while the image is wrong."""
    label_x = PAD + 30
    num_x0 = label_x + LABEL_W
    span = (W - PAD - num_x0) / 5.0
    cols = [num_x0 + span * (i + 1) - 8 for i in range(5)]   # 8px right gutter
    return {"label_x": label_x, "num_x0": num_x0, "span": span, "cols": cols,
            "dividers": [num_x0 + span * i for i in range(1, 5)]}


def _tehran(now: float) -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.fromtimestamp(now, timezone(timedelta(hours=3, minutes=30))
                                  ).strftime("%a %d %b · %H:%M Tehran")


def render_ohlc(conn, now: float | None = None) -> bytes | None:
    """The OHLC page as a PNG. Returns None when Pillow/fonts are unavailable or
    there is nothing to draw, so the caller can fall back to text."""
    if not available():
        return None
    now = now or time.time()
    groups = []
    for title, ids in _ohlc_groups():
        rows = []
        for sid in ids:
            spec = C.BY_ID.get(sid)
            if not spec:
                continue
            s = ohlc.summary(conn, sid, now)
            if not s:
                continue
            rows.append((spec, s, M.schedule_open(spec["session"], now)))
        if rows:
            groups.append((title, rows))
    if not groups:
        return None

    from PIL import Image, ImageDraw

    n_rows = sum(len(r) for _t, r in groups) + len(groups)
    height = PAD * 2 + ROW_H * 2 + n_rows * ROW_H + 10
    img = Image.new("RGB", (W, height), BG)
    d = ImageDraw.Draw(img)

    f_title, f_head, f_row = font(F_TITLE, True), font(F_HEAD, True), font(F_ROW)
    f_small = font(22)

    d.text((PAD, PAD), "IRX · OHLC", font=f_title, fill=TEXT)
    d.text((W - PAD, PAD + 8), _tehran(now), font=f_small, fill=MUTED, anchor="ra")

    y = PAD + ROW_H + 4
    g = geometry()
    label_x, cols, dividers = g["label_x"], g["cols"], g["dividers"]
    for k, head in enumerate(("open", "high", "low", "close", "range")):
        d.text((cols[k], y + 8), head, font=f_head, fill=MUTED, anchor="ra")
    y += ROW_H
    table_top = y

    for i, (title, rows) in enumerate(groups):
        d.rectangle([0, y, W, y + ROW_H], fill=BAND)
        d.text((PAD, y + 12), title.upper(), font=f_head, fill=ACCENT)
        y += ROW_H
        for j, (spec, s, is_open) in enumerate(rows):
            if (j + i) % 2:
                d.rectangle([0, y, W, y + ROW_H], fill=ALT)
            ty = y + 11
            # ● open / ○ closed, then the label (grey when the market is shut)
            d.text((PAD, ty), "●" if is_open else "○",
                   font=f_small, fill=GREEN if is_open else MUTED)
            d.text((label_x, ty), _fit(spec["label"], f_row, LABEL_W - 6),
                   font=f_row, fill=TEXT if is_open else MUTED)
            dp = spec.get("dp", 2)
            for k, key in enumerate(("open", "high", "low", "close")):
                d.text((cols[k], ty), _fmt_num(s[key], dp, f_row, g["span"] - 10),
                       font=f_row, fill=TEXT, anchor="ra")
            rng = s.get("range_pct")
            d.text((cols[4], ty), "—" if rng is None else "%.2f%%" % rng, font=f_row,
                   fill=MUTED, anchor="ra")
            y += ROW_H

    for x in dividers:
        d.line([(x, table_top), (x, y - 4)], fill=GRID, width=1)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _ohlc_groups():
    from .pages import _GROUPS
    return _GROUPS
