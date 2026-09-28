"""irx/ingest.py — the two cadences.

  intl  5 min   : every world series, fanned out (measured 3.2 s/request, so
                  concurrency is what makes a 5-minute cadence possible at all)
  iran  15 min  : one BrsAPI call feeds every Iranian series, then the raw payload
                  is archived so undisplayed currencies/coins stay recoverable

Every sample lands on a regular grid (ts floored to the cadence), because the
OHLC and trend maths downstream assume even spacing. A failed fetch is recorded
as a failure counter, never as a fake value: the panel shows a gap.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import config as C, store as S
from .sources import biquote, brsapi, others

_STALE_FACTOR = 2.0          # src_ts older than 2x the cadence -> stale flag


def _grid(now: float, minutes: int) -> int:
    step = minutes * 60
    return int(now) // step * step


def _providers():
    return others.load_providers()


def fetch_series(spec: dict, now: float, crosscheck: bool = True) -> dict:
    """Try each provider in order. Index 0 that succeeds becomes the canonical value;
    the rest are stored under '<id>@<provider>' so deviations stay queryable."""
    provs = _providers()
    obs, used, errors = None, None, []
    for provider, symbol in spec["sources"]:
        fn = provs.get(provider)
        if fn is None:
            continue
        if obs is not None and not crosscheck:
            break
        try:
            o = fn(symbol)
        except Exception as e:                      # noqa: BLE001 - provider-agnostic
            errors.append("%s/%s: %s" % (provider, symbol, str(e)[:80]))
            continue
        o["provider"] = provider
        o["symbol"] = symbol
        if obs is None:
            obs, used = o, provider
        else:
            o["crosscheck_of"] = spec["id"]
            errors.append(None)
            obs.setdefault("crosschecks", []).append(o)
    return {"id": spec["id"], "obs": obs, "used": used, "errors": [e for e in errors if e]}


def run_intl(conn, now: float | None = None, crosscheck: bool = True) -> dict:
    now = now or time.time()
    ts = _grid(now, C.INTL_INTERVAL_MIN)
    specs = [s for s in C.SERIES if s["cadence"] == "intl"]
    out = {"ts": ts, "ok": 0, "fail": [], "crosschecks": 0}
    with ThreadPoolExecutor(max_workers=max(1, C.FETCH_CONCURRENCY)) as ex:
        for res in ex.map(lambda s: fetch_series(s, now, crosscheck), specs):
            spec = C.BY_ID[res["id"]]
            o = res["obs"]
            if o is None:
                out["fail"].append({"id": res["id"], "errors": res["errors"]})
                S.bump_failures(conn, res["id"])
                continue
            src_age = (now - o["src_ts"]) if o.get("src_ts") else None
            stale = bool(o.get("stale")) or (
                src_age is not None and src_age > _STALE_FACTOR * C.INTL_INTERVAL_MIN * 60)
            S.record(conn, res["id"], o["value"], ts=ts, src_ts=o.get("src_ts"),
                     source=o["source"], stale=stale)
            for x in o.get("crosschecks", []) or []:
                S.record(conn, "%s@%s" % (res["id"], x["provider"]), x["value"], ts=ts,
                         src_ts=x.get("src_ts"), source=x["source"], stale=bool(x.get("stale")))
                out["crosschecks"] += 1
            S.clear_failures(conn, res["id"])
            out["ok"] += 1
    return out


def run_iran(conn, now: float | None = None) -> dict:
    """All Iranian series from one BrsAPI payload. Staleness is judged against the
    feed's own src_time: a frozen src_time means the market is shut, whatever the
    published schedule says (holidays resolve themselves)."""
    now = now or time.time()
    ts = _grid(now, C.IRAN_INTERVAL_MIN)
    out = {"ts": ts, "ok": 0, "fail": [], "src_frozen": False}
    try:
        flat = brsapi.snapshot(force=True)
    except Exception as e:                              # noqa: BLE001
        for s in C.SERIES:
            if s["cadence"] == "iran":
                S.bump_failures(conn, s["id"])
        out["fail"].append({"id": "brsapi", "errors": [str(e)[:120]]})
        return out

    newest = max((v.get("t") or 0) for v in flat.values())
    out["src_newest"] = newest
    out["src_frozen"] = (now - newest) > 10 * 60
    for s in C.SERIES:
        if s["cadence"] != "iran":
            continue
        sym = s["sources"][0][1] if s["sources"] else None
        it = flat.get(sym) if sym else None
        if not it:
            out["fail"].append({"id": s["id"], "errors": ["symbol %s absent" % sym]})
            S.bump_failures(conn, s["id"])
            continue
        S.record(conn, s["id"], it["price"], ts=ts, src_ts=it.get("t"), source="brsapi",
                 stale=out["src_frozen"])
        S.clear_failures(conn, s["id"])
        out["ok"] += 1
    try:
        brsapi.archive_raw()
    except Exception:                                   # noqa: BLE001
        pass
    return out


def backfill_bars(conn, intervals=("5m", "15m", "1h"), count: int = 500) -> dict:
    """Bars straight from biquote's /ohlc, so OHLC and trend pages work on day one
    instead of waiting days for samples to accumulate.

    Fetches in worker threads, WRITES IN ONE THREAD. sqlite3 connections are bound
    to the thread that made them, so a worker writing to `conn` raises
    "SQLite objects created in a thread can only be used in that thread" — which is
    also the one-writer discipline the design claims, so the fix is structural
    rather than a check_same_thread flag.
    """
    specs = [s for s in C.SERIES if s["sources"] and s["sources"][0][0] == "biquote"]
    out = {"bars": 0, "series": 0, "errors": []}

    def one(spec):
        got, errs = [], []
        for iv in intervals:
            try:
                for b in biquote.ohlc(spec["sources"][0][1], iv, count):
                    got.append((iv, b))
            except Exception as e:                          # noqa: BLE001
                errs.append("%s/%s: %s" % (spec["id"], iv, str(e)[:70]))
        return spec["id"], got, errs

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, specs))

    for sid, got, errs in results:
        out["errors"].extend(errs)
        if not got:
            continue
        n = 0
        for iv, (ots, o, h, l, c, vol) in got:
            S.put_bar(conn, sid, iv, ots, o=o, h=h, l=l, c=c, n=vol, src="biquote")
            n += 1
        conn.commit()
        if n:
            out["series"] += 1
            out["bars"] += n
    return out


def prune(conn) -> dict:
    return S.prune(conn)
