"""irx/sources/biquote.py — primary international feed.

Keyless, no signup, MT5-backed. Measured 2026-09-28 from tr:
  GET /api/{symbol}            -> mid, bid, ask, quoteAgeSeconds, stale, marketState,
                                  dayDiffPercent, high, low, timestamp, source
  GET /api/{symbol}/ohlc?interval=1m|5m|15m|30m|1h|4h|1d&count=N -> OHLC bars
Latency is ~3.2 s per request, so callers must fan out concurrently.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..net import UA_BROWSER, FetchError, get  # type: ignore

BASE = "https://biquote.io/api"
VALID_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


def _ts(s: str | None):
    if not s:
        return None
    try:
        return int(datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                   .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def quote(symbol: str, timeout: int = 30) -> dict:
    """One symbol -> normalised observation. Raises FetchError when unavailable."""
    d = get("%s/%s" % (BASE, symbol), ua=UA_BROWSER, timeout=timeout)
    if not isinstance(d, dict) or d.get("mid") is None:
        raise FetchError("biquote: no data for %s" % symbol)
    return {
        "value": float(d["mid"]),
        "src_ts": _ts(d.get("timestamp")),
        "source": "biquote:%s" % d.get("exchange", "?"),
        "stale": bool(d.get("stale")),
        "extra": {"bid": d.get("bid"), "ask": d.get("ask"), "state": d.get("marketState"),
                  "day_pct": d.get("dayDiffPercent"), "high": d.get("high"), "low": d.get("low"),
                  "age": d.get("quoteAgeSeconds"), "desc": d.get("description")},
    }


def ohlc(symbol: str, interval: str = "5m", count: int = 500, timeout: int = 40) -> list:
    """[(open_ts, o, h, l, c, tick_volume)] — bars come ready-made, no waiting for history."""
    if interval not in VALID_INTERVALS:
        raise ValueError("interval must be one of %s" % (VALID_INTERVALS,))
    d = get("%s/%s/ohlc?interval=%s&count=%d" % (BASE, symbol, interval, count),
            ua=UA_BROWSER, timeout=timeout)
    bars = (d or {}).get("bars") or []
    out = []
    for b in bars:
        ts = _ts(b.get("openTime"))
        if ts is None:
            continue
        out.append((ts, b.get("open"), b.get("high"), b.get("low"), b.get("close"),
                    b.get("tickVolume")))
    return out


def health(timeout: int = 15) -> dict:
    try:
        q = quote("EURUSD", timeout=timeout)
        return {"ok": True, "age": int(time.time() - (q["src_ts"] or 0))}
    except Exception as e:
        return {"ok": False, "err": str(e)}
