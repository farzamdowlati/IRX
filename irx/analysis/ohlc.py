"""irx/analysis/ohlc.py — OHLC for the OHLC page.

Two sources of bars, in order of preference:
  1. the `bar` table (backfilled straight from the feed's /ohlc endpoint, so the
     page works on day one instead of waiting for samples to accumulate);
  2. aggregation of our own 5-min samples, used to fill any gap the backfill
     missed and to extend coverage beyond the feed's 100-bar depth.
"""
from __future__ import annotations

import time

from .. import config as C, markets as M, store as S

INTRADAY = ("5m", "15m", "1h")


def from_samples(conn, series_id: str, bucket_sec: int, since: float,
                 until: float | None = None) -> list:
    """Aggregate raw samples into OHLC buckets. Keys: open_ts, o, h, l, c, n."""
    rows = S.window(conn, series_id, since, until)
    buckets: dict[int, dict] = {}
    for r in rows:
        if r["value"] is None:
            continue
        k = (r["ts"] // bucket_sec) * bucket_sec
        b = buckets.get(k)
        if b is None:
            buckets[k] = {"open_ts": k, "o": r["value"], "h": r["value"], "l": r["value"],
                          "c": r["value"], "n": 1, "src": "samples"}
        else:
            b["h"] = max(b["h"], r["value"])
            b["l"] = min(b["l"], r["value"])
            b["c"] = r["value"]
            b["n"] += 1
    return [buckets[k] for k in sorted(buckets)]


def bars(conn, series_id: str, interval: str = "5m", since: float | None = None,
         limit: int = 500) -> list:
    """Bars from the table, extended by sample aggregation where the table is thin."""
    out = S.get_bars(conn, series_id, interval, since=since, limit=limit)
    if out:
        return out
    if interval in INTRADAY and since is not None:
        return from_samples(conn, series_id, int(interval.rstrip("m")) * 60, since)
    return out


def day_bounds(ts: float, session: str) -> tuple:
    """Start/end of the current session-day, in the market's own timezone where the
    session names one, else UTC. Used so 'today' means the market's today."""
    rule = M.SESSION_RULES.get(session) or {}
    tzname = rule.get("tz", "UTC")
    try:
        from datetime import datetime, time, timezone
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
        d = datetime.fromtimestamp(ts, tz)
        start = datetime.combine(d.date(), time(0, 0), tzinfo=tz).timestamp()
        return start, start + 86400
    except Exception:                                       # noqa: BLE001
        start = ts - (ts % 86400)
        return start, start + 86400


def summary(conn, series_id: str, now: float | None = None) -> dict | None:
    """Today's OHLC + previous close for one series, or None when there is no data."""
    now = now or time.time()
    spec = C.BY_ID.get(series_id, {})
    session = spec.get("session", "crypto")
    start, end = day_bounds(now, session)
    b = bars(conn, series_id, "5m", since=start, limit=2000)
    if not b:
        b = bars(conn, series_id, "15m", since=start, limit=2000)
    if not b:
        return None
    o = b[0]["o"]
    h = max(x["h"] for x in b if x["h"] is not None)
    lo = min(x["l"] for x in b if x["l"] is not None)
    c = b[-1]["c"]
    prev = S.at_or_before(conn, series_id, start - 1)
    return {"series_id": series_id, "open": o, "high": h, "low": lo, "close": c,
            "n": sum(x.get("n") or 0 for x in b), "bars": len(b),
            "prev_close": prev["value"] if prev else None,
            "range_pct": ((h - lo) / o * 100.0) if o else None}


def table(conn, series_ids, now: float | None = None) -> list:
    out = []
    for sid in series_ids:
        s = summary(conn, sid, now)
        if s:
            out.append(s)
    return out
