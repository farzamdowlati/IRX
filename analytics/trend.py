#!/usr/bin/env python3
"""trend.py — deterministic asset stance: Bullish / Bearish / Sideways.

No ML, no randomness: the same history always yields the same verdict. Answers
"is this a real move or just meaningless wiggle?" by measuring the trend
efficiency ratio — how much of the asset's total travelled distance was net
progress — and testing the trend's slope against the asset's own measured noise.

Inputs: a contiguous block of (ts, price) samples for one asset. Intended to be
called on one trading session (07:00-22:00 Tehran).

Bands are calibrated per asset (see NOISE_PCT below) from measured sample
volatility, so "meaningless" means "inside one standard deviation of this
asset's own step noise" rather than a hardcoded percentage.

  python3 trend.py [--demo]        # demo needs /tmp/irx_samples.jsonl
"""
import json
import math
import statistics as st
import sys
import os

# Per-step (one sampling interval) return sigma in %, measured from real
# history on this feed. Used to convert a trend into a noise-normalised t-stat.
NOISE_PCT = {
    "usd": 0.211, "usdt": 0.273, "cny": 0.211, "aed": 0.200, "eur": 0.200,
    "g18": 0.200, "g24": 0.249, "emami": 0.279, "quarter": 0.411,
    "btc": 1.000, "eth": 1.100, "bnb": 1.100, "xrp": 1.400, "sol": 1.300,
    "xau_global": 0.300,
}
DEFAULT_NOISE = 0.300

# Verdict bands: |normalised slope| must clear MIN_T to be a real direction,
# and the path must be directional enough (MIN_ER) to not be choppy drift.
# MIN_T=2 means the trend must exceed twice the asset's own noise sigma.
MIN_T = 2.0
MIN_ER = 0.30
CONFIRM = 6         # minimum samples before a trend may be declared


def ols(prices):
    """Least-squares slope, intercept and R² for evenly spaced samples."""
    n = len(prices)
    if n < 2:
        return 0.0, prices[0] if prices else 0.0, 0.0
    t = list(range(n))
    mt, mp = st.mean(t), st.mean(prices)
    den = sum((x - mt) ** 2 for x in t)
    b = sum((t[i] - mt) * (prices[i] - mp) for i in range(n)) / den if den else 0.0
    a = mp - b * mt
    pred = [a + b * x for x in t]
    ss_res = sum((prices[i] - pred[i]) ** 2 for i in range(n))
    ss_tot = sum((prices[i] - mp) ** 2 for i in range(n))
    return b, a, (1 - ss_res / ss_tot if ss_tot else 0.0)


def efficiency_ratio(prices):
    """|net change| / total path travelled. 1 = perfect trend, 0 = pure noise."""
    steps = [(prices[i + 1] / prices[i] - 1.0) for i in range(len(prices) - 1) if prices[i]]
    path = sum(abs(s) for s in steps)
    if not path:
        return 0.0
    net = abs(prices[-1] / prices[0] - 1.0)
    # sum of simple returns is not the total gain, so the raw ratio can exceed 1;
    # clamp so 1.0 consistently means "perfectly one-directional".
    return min(1.0, net / path)


def classify(prices, noise_pct=None, min_t=MIN_T, min_er=MIN_ER, confirm=CONFIRM):
    """Deterministic stance for one asset. Returns a dict; never raises."""
    n = len(prices)
    out = {"n": n, "verdict": "Insufficient data", "net_pct": None, "momentum_pct": None,
           "er": None, "slope_pct": None, "slope_t": None, "r2": None, "bars": 0}
    if n < confirm:
        return out
    noise = noise_pct if noise_pct is not None else DEFAULT_NOISE
    net = (prices[-1] / prices[0] - 1.0) * 100.0
    m = max(1, n // 2)
    recent = (prices[-1] / prices[-m] - 1.0) * 100.0
    b, _, r2 = ols(prices)
    slope_pct = b * (n - 1) / prices[0] * 100.0 if prices[0] else 0.0  # % over the block
    # t-stat for the OLS slope: sigma_resid(price units) / sqrt(sum (t-tbar)^2).
    # noise is a per-step % sigma, converted to price units via the block's level,
    # so slope_t is "how many of this asset's own noise sigma is the drift".
    den = sum((i - (n - 1) / 2) ** 2 for i in range(n))
    sigma_eps = prices[0] * noise / 100.0 if prices[0] else 0.0
    slope_t = (b * math.sqrt(den) / sigma_eps) if sigma_eps else 0.0
    er = efficiency_ratio(prices)

    out.update({"net_pct": round(net, 2), "momentum_pct": round(recent, 2),
                "er": round(er, 3), "slope_pct": round(slope_pct, 2),
                "slope_t": round(slope_t, 2), "r2": round(r2, 3)})

    strong = abs(slope_t) >= min_t and er >= min_er
    if strong:
        out["verdict"] = "Bullish" if slope_t > 0 else "Bearish"
        strength = abs(slope_t) / min_t
        out["bars"] = 2 if strength < 1.5 else 3 if strength < 3 else 4 if strength < 6 else 5
    else:
        out["verdict"] = "Sideways"
        # choppy: a big net move that the trend engine rejects as noise
        if abs(net) >= 2 * noise:
            out["verdict"] = "Sideways (choppy, net move looks like noise)"
    return out


def label(v, bars=0):
    icon = {"Bullish": "🟢", "Bearish": "🔴", "Sideways": "⚪"}.get(v.split()[0], "⚪")
    return f"{icon} {v}" + (" " + "▲" * bars if bars else "")


def main():
    if "--demo" not in sys.argv:
        print(__doc__)
        return
    path = "/tmp/irx_samples.jsonl"
    if not os.path.exists(path):
        print("no /tmp/irx_samples.jsonl")
        return
    rows = [json.loads(l) for l in open(path) if l.strip()]
    rows.sort(key=lambda r: r["ts"])
    for k in ("usd", "usdt", "g24", "btc"):
        prices = [r[k] for r in rows if r.get(k) is not None]
        r = classify(prices, NOISE_PCT.get(k))
        print(f"{k:12} {label(r['verdict'], r['bars']):34} "
              f"net {r['net_pct']:+.2f}%  ER {r['er']}  t {r['slope_t']}  R² {r['r2']}")


if __name__ == "__main__":
    main()
