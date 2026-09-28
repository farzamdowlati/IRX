"""irx/sources/brsapi.py — Tehran street rates (one call feeds every IR series).

The whole market comes back in a single request, so it is fetched once and cached
briefly; per-series lookups then hit the cache. The raw payload is kept so the
twelve currencies and coins IRX does not display can be re-enabled without new
plumbing.
"""
from __future__ import annotations

import os
import time

from .. import config as C
from ..net import UA_BROWSER, FetchError, get  # type: ignore

URL = "https://api.brsapi.ir/Market/Gold_Currency.php?key={key}"
CACHE_TTL = 45          # seconds
_cache: dict = {"t": 0.0, "data": None, "raw": None}


def _fetch(timeout: int = 25) -> dict:
    if not C.BRS_KEY:
        raise FetchError("brsapi: BRS_API_KEY missing from .env")
    # Browser UA is mandatory here: the Iranian firewall IP-bans python's default UA.
    raw = get(URL.format(key=C.BRS_KEY), ua=UA_BROWSER, timeout=timeout)
    flat = {}
    for group in ("gold", "currency", "cryptocurrency"):
        for it in raw.get(group, []) or []:
            sym = it.get("symbol")
            if not sym:
                continue
            try:
                price = float(it["price"])
            except (TypeError, ValueError, KeyError):
                continue
            flat[sym] = {"price": price,
                         "chg": it.get("change_percent"),
                         "t": it.get("time_unix"),
                         "name": it.get("name"),
                         "group": group}
    if not flat:
        raise FetchError("brsapi: empty payload")
    return {"flat": flat, "raw": raw}


def snapshot(force: bool = False) -> dict:
    if force or _cache["data"] is None or (time.time() - _cache["t"]) > CACHE_TTL:
        d = _fetch()
        _cache.update({"t": time.time(), "data": d["flat"], "raw": d["raw"]})
    return _cache["data"]


def raw_payload() -> dict | None:
    return _cache["raw"]


def quote(symbol: str, force: bool = False) -> dict:
    """One BrsAPI symbol -> normalised observation. Never raises on a missing symbol."""
    flat = snapshot(force=force)
    it = flat.get(symbol)
    if not it:
        raise FetchError("brsapi: symbol %s absent" % symbol)
    src_ts = it.get("t")
    return {
        "value": it["price"],
        "src_ts": int(src_ts) if src_ts else None,
        "source": "brsapi",
        "stale": False,                       # staleness is judged by the caller vs src_ts
        "extra": {"day_pct": it.get("chg"), "name": it.get("name"), "group": it.get("group")},
    }


def archive_raw(dirpath: str | None = None) -> str | None:
    """Keep today's full payload on disk (gitignored) for anything we do not display."""
    raw = raw_payload()
    if not raw:
        return None
    import json
    d = dirpath or os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "data", "raw")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "brs-%d.json" % int(time.time()))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    return p


def health(timeout: int = 20) -> dict:
    try:
        flat = snapshot(force=True)
        u = flat.get("USD", {})
        return {"ok": True, "usd": u.get("price"), "src_age": int(time.time() - (u.get("t") or 0))}
    except Exception as e:
        return {"ok": False, "err": str(e)}
