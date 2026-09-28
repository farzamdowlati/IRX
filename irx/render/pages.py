"""irx/render/pages.py — the navigable message's pages.

Layout (chosen by the user, 2026-09-28): grouped blocks, aligned label/value/change,
a closed-market badge, and ONE number per row (change vs previous close) rather than
a past-hour delta on top. Rows live inside <pre> so the monospace font actually
aligns the columns — with the default proportional font, fixed-width padding is
invisible padding.

Rule of the house: every number here is computed by us from stored samples, and any
claim carries its tier (identity / statistic) or its sample size. Nothing on these
pages is generated text.
"""
from __future__ import annotations

import hashlib
import time

from .. import config as C, markets as M, store as S
from ..analysis import cross, ohlc, trend

PAGE_PRICES = "prices"
PAGE_OHLC = "ohlc"
PAGE_CROSS = "cross"
PAGE_TREND = "trend"
PAGE_ORDER = (PAGE_PRICES, PAGE_OHLC, PAGE_CROSS, PAGE_TREND)

_GROUPS = [
    ("Indices", ["NASDAQ", "SP500", "DJI", "NIKKEI", "HK", "DAX", "FTSE", "ASX"]),
    ("FX & dollar", ["EURUSD", "USDJPY", "USDCNY", "USDAED", "DXY"]),
    ("Energy & metals", ["WTI", "BRENT", "XAUUSD", "XAGUSD"]),
    ("Crypto (24/7)", ["BTCUSD"]),
    ("Tehran street", ["USDIRT", "USDTIRT", "G18", "G_ABSHODE", "EMAMI",
                       "AEDIRT", "CNYIRT", "JPYIRT", "EURIRT"]),
]

LBL_W, VAL_W, PCT_W = 13, 13, 8


def h(s: str) -> str:
    """Escape for Telegram HTML parse mode."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(v, dp: int) -> str:
    if v is None:
        return "—"
    return "{:,.{}f}".format(v, dp)


def _pct(v) -> str:
    if v is None:
        return "—"
    return "{:+.2f}%".format(v)


def _tehran(now: float) -> str:
    from datetime import datetime, timedelta, timezone
    d = datetime.fromtimestamp(now, timezone(timedelta(hours=3, minutes=30)))
    return d.strftime("%a %d %b · %H:%M Tehran")


def _pad_label(label: str, width: int = LBL_W) -> str:
    # Pad on the RAW width, then escape: escaping first adds 4 characters for the
    # ampersand entity and shifts that row's value column once Telegram renders the
    # entity back to a single character (seen live on the S&P row).
    raw = str(label)[:width]
    return h(raw) + " " * max(0, width - len(raw))


def _badge(conn, spec: dict, now: float) -> str:
    """⏸ scheduled-closed · ⚠ suspiciously stale · blank = live."""
    r = S.latest(conn, spec["id"])
    sess = spec["session"]
    if sess == "crypto":
        return " ⚠" if (r and r["stale"]) else ""
    if not M.schedule_open(sess, now):
        return " ⏸"
    if r and r["stale"]:
        return " ⚠"
    return ""


def _row(conn, spec: dict, now: float) -> str:
    r = S.latest(conn, spec["id"])
    v = r["value"] if r else None
    dp = spec.get("dp", 2)
    pct = ohlc.day_change(conn, spec["id"], spec["session"], v, now) if v is not None else None
    # Pad on the RAW width, then escape the label only. Escaping first would add 4
    # characters for "&amp;" and shift that row's value column out of alignment once
    # Telegram renders the entity back to a single "&" (seen live on the S&P row).
    raw = spec["label"][:LBL_W]
    label_cell = h(raw) + " " * max(0, LBL_W - len(raw))
    return "%s%*s  %*s%s" % (label_cell, VAL_W, _num(v, dp), PCT_W, _pct(pct),
                             _badge(conn, spec, now))


def _session_line(conn, now: float) -> str:
    parts = []
    for sess, name in (("iran", "Tehran"), ("cash:US", "New York"), ("cash:JP", "Tokyo"),
                       ("cash:HK", "HK"), ("cash:UK", "London")):
        open_ = M.schedule_open(sess, now)
        parts.append("%s %s" % (name, "🔵" if open_ else "⚪"))
    return "  ".join(parts) + "   (🔵 open · ⚪ closed)"


def header(now: float) -> str:
    return "📊 <b>IRX</b> · " + _tehran(now)


# --------------------------------------------------------------------- prices
def prices(conn, now: float | None = None) -> str:
    now = now or time.time()
    out = [header(now), ""]
    for title, ids in _GROUPS:
        rows = [_row(conn, C.BY_ID[i], now) for i in ids if i in C.BY_ID]
        if not rows:
            continue
        out.append("◆ <b>%s</b>" % h(title))
        out.append("<pre>" + "\n".join(rows) + "</pre>")
    iran_closed = not M.schedule_open("iran", now)
    if iran_closed:
        out.append("Tehran is closed — street rows are the last close; "
                   "USDT and BTC stay live.")
    out.append(_session_line(conn, now))
    out.append("<i>change = vs previous close, computed from stored samples; "
               "⏸ closed · ⚠ no fresh data</i>")
    return "\n".join(out)


# ----------------------------------------------------------------------- ohlc
def _abbr(v, dp: int = 2) -> str:
    """Compact number for the narrow text fallback: exact below 100k, K/M above
    (same rule as the image renderer)."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e6:
        return "%.2fM" % (v / 1e6)
    if a >= 1e5:
        return "%.1fK" % (v / 1e3)
    return "{:,.{}f}".format(v, dp)


def ohlc_caption(conn, now: float | None = None) -> str:
    """Caption for the OHLC IMAGE. Kept short: Telegram caps media captions at 1024
    characters, and the table itself is in the picture."""
    now = now or time.time()
    counts = {"world": 0, "iran": 0}
    for sid, spec in C.BY_ID.items():
        if spec.get("render", True) and ohlc.summary(conn, sid, now):
            counts["iran" if spec["group"] == "iran" else "world"] += 1
    return ("📈 <b>OHLC</b> · today, in each market's own day · %s\n"
            "<i>O/H/L/C then range = (high−low)/open · ● open ○ closed · "
            "K = thousand, M = million\n"
            "world: the feed's own candles (%d series) · Tehran: our 15-min samples "
            "(%d series, thin until history builds)</i>"
            % (_tehran(now), counts["world"], counts["iran"]))


def ohlc_page(conn, now: float | None = None) -> str:
    """NARROW TEXT FALLBACK, used only when the PNG renderer is unavailable.

    Four wide columns cannot fit a phone (they wrap and the table collapses), so each
    series gets a three-line block: the label, then O/H, then L/C. Every line stays
    under 30 characters, which is the only way a monospace block survives a phone's
    line width. The image path (render/imagetable.py) is the primary one.
    """
    now = now or time.time()
    out = ["📈 <b>OHLC</b> · today · <i>O/H/L/C, K = thousand, M = million</i>"]
    for title, ids in _GROUPS:
        blocks = []
        for sid in ids:
            spec = C.BY_ID.get(sid)
            s = ohlc.summary(conn, sid, now) if spec else None
            if not s:
                continue
            p = spec.get("dp", 2)
            badge = "" if M.schedule_open(spec["session"], now) else " ⏸"
            blocks.append("%s%s\n  O %s  H %s\n  L %s  C %s" % (
                _abbr_label(spec["label"]), badge, _abbr(s["open"], p), _abbr(s["high"], p),
                _abbr(s["low"], p), _abbr(s["close"], p)))
        if blocks:
            out.append("◆ <b>%s</b>" % h(title))
            out.append("<pre>" + "\n".join(blocks) + "</pre>")
    return "\n".join(out)


def _abbr_label(label: str, width: int = 16) -> str:
    return label if len(label) <= width else label[:width - 1] + "…"


# ---------------------------------------------------------------------- cross
def _leg_lines(tri: dict) -> list:
    out = []
    for leg in tri["legs"]:
        out.append("  %-22s %14s  %+6.2f%%" % (leg["name"][:22], _num(leg["value"], 0),
                                               leg["dev_pct"]))
    return out


def cross_page(conn, now: float | None = None) -> str:
    now = now or time.time()
    out = ["🔀 <b>Cross-market</b> · " + _tehran(now), ""]
    tri = cross.triangulation(conn)
    if tri:
        out.append("◆ <b>One dollar, %d independent quotes</b>  <i>[Tier A · identity]</i>"
                   % len(tri["legs"]))
        out.append("<pre>" + "\n".join(_leg_lines(tri)) + "</pre>")
        out.append("<pre>%s\n%s</pre>" % (
            "  %-22s %14s" % ("consensus (median)", _num(tri["consensus"], 0)),
            "  %-22s %13.2f%%   max-min %.2f%%" % ("dispersion (MAD)", tri["dispersion_mad_pct"],
                                                   tri["spread_pct"])))
        out.append("<i>the same dollar priced %d ways; the spread IS the arbitrage. "
                   "A leg breaking away names the dislocated market.</i>" % len(tri["legs"]))
    gaps = cross.parity_gaps(conn)
    if gaps:
        out.append("")
        out.append("◆ <b>Parity gaps</b>  <i>[Tier A · identity]</i>")
        rows = []
        for g in gaps:
            hl = S.latest(conn, "gap:%s" % g["label"])
            extra = ""
            if hl and hl["value"] is not None:
                extra = "  now %+.2f%%" % hl["value"]
            rows.append("  %-14s %+7.2f%%  (implied %s T)%s"
                        % (g["label"], g["gap_pct"], _num(g["implied_usdirt"], 0), extra))
        gp = cross.gold_parity(conn)
        if gp:
            rows.append("  %-14s %+7.2f%%  (Tehran gram %s vs world %s T)"
                        % ("Gold", gp["gap_pct"], _num(gp["tehran_pure_gram"], 0),
                           _num(gp["world_gram_tmn"], 0)))
            if gp.get("melt_purity"):
                rows.append("  %-14s   %.4f   (ab-shode mesghal / pure gold)"
                            % ("Melt purity", gp["melt_purity"]))
            if gp.get("coin_premium_pct") is not None:
                rows.append("  %-14s %+7.2f%%  (Emami over melt value)"
                            % ("Coin premium", gp["coin_premium_pct"]))
        tp = cross.tether_premium(conn)
        if tp is not None:
            rows.append("  %-14s %+7.2f%%  (USDT vs street cash USD)" % ("Tether", tp))
        out.append("<pre>" + "\n".join(rows) + "</pre>")
        out.append("<i>a gap is a real spread only because both sides are the same "
                   "underlying priced in the same dollar.</i>")
    out.append("")
    out.append("◆ <b>Statistical</b>  <i>[Tier B · measured, not causal]</i>")
    pairs = [("USDIRT", "WTI"), ("USDIRT", "SP500"), ("USDIRT", "DXY"),
             ("USDIRT", "BTCUSD"), ("USDTIRT", "USDIRT")]
    rows = []
    for a, b in pairs:
        c = cross.rolling_corr(conn, a, b, now, hours=24)
        if c:
            rows.append("  %-22s r=%+5.2f  n=%-4d (%.0fh)"
                        % ("%s vs %s" % (a, b), c["r"], c["n"], c["hours"]))
    if rows:
        out.append("<pre>" + "\n".join(rows) + "</pre>")
    else:
        out.append("<i>not enough paired history yet — every Tier B line states its n, "
                   "and none is shown until it has one.</i>")
    hl = cross.half_life(conn, "gap:tether", now)
    if hl and hl.get("half_life_h"):
        out.append("<i>tether gap typically halves in %.1fh (AR1 t=%.1f, n=%d)</i>"
                   % (hl["half_life_h"], hl["t"] or 0, hl["n"]))
    return "\n".join(out)


# ---------------------------------------------------------------------- trend
def trend_page(conn, now: float | None = None, hours: float = 24.0) -> str:
    now = now or time.time()
    out = ["📉 <b>Trend · last %dh</b> · %s" % (hours, _tehran(now)), ""]
    for title, ids in _GROUPS:
        rows = []
        for sid in ids:
            r = trend.for_series(conn, sid, now, hours=hours)
            if r["verdict"] == "Insufficient data":
                continue
            rows.append("  %-13s %-26s net %+6.2f%%  ER %.2f  t %+5.2f  n=%d"
                        % (sid, trend.label(r["verdict"], r["bars"]), r["net_pct"],
                           r["er"] or 0, r["slope_t"] or 0, r["n"]))
        if rows:
            out.append("◆ <b>%s</b>" % h(title))
            out.append("<pre>" + "\n".join(rows) + "</pre>")
    if len(out) <= 3:
        out.append("<i>no series has the minimum %d samples yet.</i>" % trend.CONFIRM)
    return "\n".join(out)


# --------------------------------------------------------------------- router
_RENDERERS = {PAGE_PRICES: prices, PAGE_OHLC: ohlc_page, PAGE_CROSS: cross_page,
              PAGE_TREND: trend_page}


def render(conn, page: str = PAGE_PRICES, now: float | None = None) -> str:
    fn = _RENDERERS.get(page) or _RENDERERS[PAGE_PRICES]
    text = fn(conn, now)
    return text[:4000]


def signature(conn, page: str = PAGE_PRICES, now: float | None = None) -> str:
    """Content hash of a page — the hourly job skips the edit when nothing changed and
    every market on the page is closed (user's rule, 2026-09-28)."""
    return hashlib.sha1(render(conn, page, now).encode("utf-8")).hexdigest()[:12]


def all_closed(conn, page: str = PAGE_PRICES, now: float | None = None) -> bool:
    """True when no series on the page is in a 24/7 session and every scheduled
    market is shut."""
    now = now or time.time()
    ids = [i for _t, group in _GROUPS for i in group]
    for sid in ids:
        spec = C.BY_ID.get(sid)
        if not spec:
            continue
        if spec["session"] == "crypto" or M.schedule_open(spec["session"], now):
            return False
    return True
