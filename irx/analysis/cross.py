"""irx/analysis/cross.py — cross-market checks, graded by what they may claim.

TIER A (identity): two quotes of the same underlying, or a fixed peg. A deviation
*is* a real spread — law of one price, no causal story required.
  - parity gaps: Tehran-implied cross vs the world cross (CNY, EUR, AED, JPY)
  - gold parity: world ounce -> Toman gram, vs the Tehran 18k gram (/0.75 = pure)
  - coin premium over melt value
  - tether premium: USDT vs street cash USD
  - **D1 dollar triangulation**: every dollar-denominated instrument in the data
    yields an independent implied USDIRT; consensus = median, disagreement =
    dispersion. Validated live: five legs agreed to 0.55% (2026-09-28).

TIER B (statistic): measured from our own history, always reported with its
window and n, and always labelled as non-causal.

TIER C: narrative. Not implemented here, by design.
"""
from __future__ import annotations

import math
import statistics as st

from .. import config as C, markets as M, store as S

GRAM_PER_OZ = 31.1034768
MESGHAL_G = 4.6083              # measured: IR_GOLD_MELTED is a mesghal quote (see memo)
K18 = 0.75
COIN_GRAM, COIN_PURITY = 8.105, 0.900


def _v(conn, series_id):
    r = S.latest(conn, series_id)
    return r["value"] if r else None


def pure_gram(g18):
    """24k-equivalent gram price. BrsAPI's own 24k series is just this, so we derive it
    rather than store a redundant series."""
    return g18 / K18 if g18 else None


# --------------------------------------------------------------------- tier A
def parity_gaps(conn) -> list:
    """Tehran-implied cross vs world cross. `per` handles quotes per-100 units (JPY)."""
    usd = _v(conn, "USDIRT")
    out = []
    checks = [("CNY", "CNYIRT", "USDCNY", 1.0, "Yuan"),
              ("EUR", "EURIRT", "EURUSD", 1.0, "Euro"),
              ("AED", "AEDIRT", "USDAED", 1.0, "Dirham"),
              ("JPY", "JPYIRT", None, 100.0, "Yen (per 100)")]
    for _code, local_id, world_id, per, label in checks:
        local = _v(conn, local_id)
        if not (usd and local):
            continue
        if _code == "EUR":
            # local is Toman per EUR; world is USD per EUR -> implied Toman per USD
            w = _v(conn, "EURUSD")
            implied = local / w / per if w else None
        elif _code == "JPY":
            w = _v(conn, "USDJPY")
            # local is Toman per 100 JPY; world is JPY per USD
            implied = (local / per) * w if w else None
        else:
            w = _v(conn, world_id)
            implied = (local / per) * w if w else None
        if implied:
            out.append({"label": label, "implied_usdirt": implied, "world": w,
                        "gap_pct": (implied / usd - 1) * 100.0, "tier": "A"})
    return out


def gold_parity(conn) -> dict | None:
    xau = _v(conn, "XAUUSD")
    usd = _v(conn, "USDIRT")
    g18 = _v(conn, "G18")
    if not (xau and usd and g18):
        return None
    theo = xau / GRAM_PER_OZ * usd
    pg = pure_gram(g18)
    melt = _v(conn, "G_ABSHODE")
    out = {"world_gram_tmn": theo, "tehran_pure_gram": pg,
           "gap_pct": (pg / theo - 1) * 100.0, "tier": "A"}
    if melt:
        out["melt_mesghal_tmn"] = melt
        out["melt_purity"] = melt / (pg * MESGHAL_G) if pg else None
    emami = _v(conn, "EMAMI")
    if emami and pg:
        out["coin_melt_tmn"] = pg * COIN_GRAM * COIN_PURITY
        out["coin_premium_pct"] = (emami / out["coin_melt_tmn"] - 1) * 100.0
    return out


def tether_premium(conn) -> float | None:
    usd, usdt = _v(conn, "USDIRT"), _v(conn, "USDTIRT")
    return (usdt / usd - 1) * 100.0 if (usd and usdt) else None


def triangulation(conn) -> dict | None:
    """D1: every dollar leg, one consensus, one dispersion gauge."""
    usd = _v(conn, "USDIRT")
    usdt = _v(conn, "USDTIRT")
    g18 = _v(conn, "G18")
    xau = _v(conn, "XAUUSD")
    btc_tmn = _v(conn, "BTCIRT")
    btc_usd = _v(conn, "BTCUSD")
    legs = []
    if usd:
        legs.append(("street cash USD", usd, "brsapi"))
    if usdt:
        legs.append(("USDT", usdt, "brsapi/wallex"))
    if g18 and xau:
        legs.append(("gold 18k vs world oz", pure_gram(g18) / (xau / GRAM_PER_OZ), "derived"))
    if btc_tmn and btc_usd:
        legs.append(("BTC Toman vs world BTC", btc_tmn / btc_usd, "wallex/biquote"))
    if len(legs) < 2:
        return None
    vals = [v for _, v, _ in legs]
    med = st.median(vals)
    mad = st.median([abs(v - med) for v in vals])
    return {
        "consensus": med,
        "dispersion_mad_pct": mad / med * 100.0,
        "spread_pct": (max(vals) - min(vals)) / med * 100.0,
        "legs": [{"name": n, "value": v, "source": s, "dev_pct": (v / med - 1) * 100.0}
                 for n, v, s in legs],
        "tier": "A",
    }


# --------------------------------------------------------------------- tier B
def robust_z(values: list) -> float | None:
    """Median/MAD z-score — survives fat tails, unlike mean/std."""
    if len(values) < 12:
        return None
    med = st.median(values)
    mad = st.median([abs(v - med) for v in values])
    if not mad:
        return None
    return (values[-1] - med) / (1.4826 * mad)


def series(conn, series_id: str, days: float, now: float, session_only: bool = False) -> list:
    """Values over a trailing window; `session_only` keeps rows where the market was
    actually open (essential for Iran: closed hours repeat a price and fake persistence)."""
    since = now - days * 86400
    rows = S.window(conn, series_id, since, now)
    if session_only:
        spec = C.BY_ID.get(series_id, {})
        sess = spec.get("session", "crypto")
        rows = [r for r in rows if M.schedule_open(sess, r["ts"])]
    return [r["value"] for r in rows if r["value"] is not None]


def half_life(conn, series_id: str, now: float, days: float = 14.0,
              session_only: bool = True) -> dict | None:
    """D2: AR(1) mean-reversion half-life of a GAP series (not a price level).

    The caller passes a series whose values are already deviations, so a stationary
    quantity is fit rather than a trending level. Returns the half-life in hours plus
    the AR(1) t-stat and the sample size, so a short sample cannot masquerade as a
    precise constant.
    """
    vals = series(conn, series_id, days, now, session_only=session_only)
    if len(vals) < 30:
        return None
    x, y = vals[:-1], vals[1:]
    mx, my = st.mean(x), st.mean(y)
    den = sum((v - mx) ** 2 for v in x)
    if not den:
        return None
    b = sum((x[i] - mx) * (y[i] - my) for i in range(len(x))) / den
    resid = [y[i] - (my + b * (x[i] - mx)) for i in range(len(x))]
    sd = st.pstdev(resid)
    se = sd / math.sqrt(den) if den else 0.0
    rows = S.window(conn, series_id, now - days * 86400, now)
    step = st.median([rows[i + 1]["ts"] - rows[i]["ts"] for i in range(len(rows) - 1)]) \
        if len(rows) > 1 else 300
    hl_h = (-math.log(2) / math.log(b)) * step / 3600.0 if 0 < b < 1 else None
    return {"n": len(vals), "b": b, "t": (b / se) if se else None, "half_life_h": hl_h,
            "sd": sd, "step_sec": step, "tier": "B"}


def rolling_corr(conn, id_a: str, id_b: str, now: float, hours: float = 24.0) -> dict | None:
    """D-note: Pearson on RETURNS, reported with n and window. Never called 'cause'."""
    since = now - hours * 3600
    ra = {r["ts"]: r["value"] for r in S.window(conn, id_a, since, now) if r["value"]}
    rb = {r["ts"]: r["value"] for r in S.window(conn, id_b, since, now) if r["value"]}
    ts = sorted(set(ra) & set(rb))
    if len(ts) < 12:
        return None
    a = [ra[ts[i + 1]] / ra[ts[i]] - 1 for i in range(len(ts) - 1) if ra[ts[i]]]
    b = [rb[ts[i + 1]] / rb[ts[i]] - 1 for i in range(len(ts) - 1) if rb[ts[i]]]
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = st.mean(a), st.mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((x - mb) ** 2 for x in b))
    if not den:
        return None
    return {"r": num / den, "n": n, "hours": hours, "tier": "B",
            "label": "%s vs %s" % (id_a, id_b)}
