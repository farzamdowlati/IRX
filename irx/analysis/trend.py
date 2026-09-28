"""irx/analysis/trend.py — deterministic stance per series (Bullish/Bearish/Sideways).

No ML, no randomness, no LLM: the same history must always give the same verdict.
The question it answers is "is this a real move or meaningless wiggle?" by testing
the OLS slope against the asset's OWN measured per-step noise, and requiring the
path to be directional (efficiency ratio) rather than choppy drift.

Carried over from the v1 analytics/trend.py engine, with one change: the noise
floor is MEASURED from the stored samples instead of hardcoded per asset, so a
series added later needs no calibration table.
"""
from __future__ import annotations

import math
import statistics as st

from .. import config as C, markets as M, store as S

MIN_T = 2.0            # slope must exceed twice the asset's own noise sigma
MIN_ER = 0.30          # and the path must be directional, not choppy
CONFIRM = 6            # minimum samples before a verdict may be declared
NOISE_FLOOR_PCT = 0.02  # guard against a degenerate zero-noise sample


def ols(prices):
    n = len(prices)
    if n < 2:
        return 0.0, prices[0] if prices else 0.0, 0.0
    t = list(range(n))
    mt, mp = st.mean(t), st.mean(prices)
    den = sum((x - mt) ** 2 for x in t)
    b = sum((t[i] - mt) * (prices[i] - mp) for i in range(n)) / den if den else 0.0
    a = mp - b * mt
    ss_res = sum((prices[i] - (a + b * i)) ** 2 for i in range(n))
    ss_tot = sum((prices[i] - mp) ** 2 for i in range(n))
    return b, a, (1 - ss_res / ss_tot if ss_tot else 0.0)


def efficiency_ratio(prices):
    steps = [(prices[i + 1] / prices[i] - 1.0) for i in range(len(prices) - 1) if prices[i]]
    path = sum(abs(s) for s in steps)
    if not path:
        return 0.0
    return min(1.0, abs(prices[-1] / prices[0] - 1.0) / path)


def step_noise_pct(prices) -> float:
    """Robust per-step return sigma in %, from the asset's own samples."""
    steps = [(prices[i + 1] / prices[i] - 1.0) * 100.0
             for i in range(len(prices) - 1) if prices[i]]
    if len(steps) < 5:
        return NOISE_FLOOR_PCT
    med = st.median(steps)
    mad = st.median([abs(s - med) for s in steps])
    sigma = 1.4826 * mad
    return max(NOISE_FLOOR_PCT, sigma)


def classify(prices, noise_pct: float | None = None, min_t: float = MIN_T,
             min_er: float = MIN_ER, confirm: int = CONFIRM) -> dict:
    n = len(prices)
    out = {"n": n, "verdict": "Insufficient data", "net_pct": None, "momentum_pct": None,
           "er": None, "slope_pct": None, "slope_t": None, "r2": None, "noise_pct": None,
           "bars": 0}
    if n < confirm:
        return out
    noise = noise_pct if noise_pct is not None else step_noise_pct(prices)
    net = (prices[-1] / prices[0] - 1.0) * 100.0
    m = max(1, n // 2)
    recent = (prices[-1] / prices[-m] - 1.0) * 100.0
    b, _, r2 = ols(prices)
    slope_pct = b * (n - 1) / prices[0] * 100.0 if prices[0] else 0.0
    den = sum((i - (n - 1) / 2) ** 2 for i in range(n))
    sigma_eps = prices[0] * noise / 100.0 if prices[0] else 0.0
    slope_t = (b * math.sqrt(den) / sigma_eps) if sigma_eps else 0.0
    er = efficiency_ratio(prices)
    out.update({"net_pct": round(net, 2), "momentum_pct": round(recent, 2),
                "er": round(er, 3), "slope_pct": round(slope_pct, 2),
                "slope_t": round(slope_t, 2), "r2": round(r2, 3),
                "noise_pct": round(noise, 3)})
    if abs(slope_t) >= min_t and er >= min_er:
        out["verdict"] = "Bullish" if slope_t > 0 else "Bearish"
        strength = abs(slope_t) / min_t
        out["bars"] = 2 if strength < 1.5 else 3 if strength < 3 else 4 if strength < 6 else 5
    else:
        out["verdict"] = "Sideways"
        if abs(net) >= 2 * noise:
            out["verdict"] = "Sideways (choppy)"
    return out


def label(v: str, bars: int = 0) -> str:
    icon = {"Bullish": "🟢", "Bearish": "🔴", "Sideways": "⚪"}.get(v.split()[0], "⚪")
    return "%s %s%s" % (icon, v, (" " + "▲" * bars) if bars else "")


def for_series(conn, series_id: str, now: float, hours: float = 24.0,
               session_only: bool = True) -> dict:
    """Verdict for one series over a trailing window. Closed-market rows repeat a
    price and would fake persistence, so they are dropped for scheduled markets."""
    spec = C.BY_ID.get(series_id, {})
    session = spec.get("session", "crypto")
    rows = S.window(conn, series_id, now - hours * 3600, now)
    if session_only and session != "crypto":
        rows = [r for r in rows if M.schedule_open(session, r["ts"])]
    prices = [r["value"] for r in rows if r["value"] is not None]
    res = classify(prices)
    res["series_id"] = series_id
    res["hours"] = hours
    return res
