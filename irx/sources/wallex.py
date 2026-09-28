"""irx/sources/wallex.py — Toman-denominated crypto from an Iranian venue.

Why this exists: BrsAPI's crypto symbols are WORLD USD prices (its `BTC` is
~83k, i.e. dollars), so the BTC leg of the dollar triangulation needs a
Toman-denominated BTC price from a local exchange. Wallex supplies it, plus a
second independent USDT/Toman venue.

Measured 2026-09-28:
  /v1/depth?symbol=USDTTMN  -> 146 ms from the production VPS, best bid/ask + levels
  /v1/markets               -> 46 s cold from the VPS  <-- never call this in a cycle
Nobitex (api.nobitex.ir) does not resolve from either host — not used.
Bitpin works too and is kept as the fallback venue.
"""
from __future__ import annotations

import time

from ..net import UA_BROWSER, FetchError, get  # type: ignore

DEPTH = "https://api.wallex.ir/v1/depth?symbol={}"
BITPIN_TICKER = "https://api.bitpin.ir/api/v1/mkt/tickers/"


def quote(symbol: str, timeout: int = 20) -> dict:
    """Best bid/ask mid from the live order book. `symbol` e.g. USDTTMN, BTCTMN."""
    d = get(DEPTH.format(symbol), ua=UA_BROWSER, timeout=timeout)
    res = (d or {}).get("result") or {}
    asks, bids = res.get("ask") or [], res.get("bid") or []
    if not asks or not bids:
        raise FetchError("wallex: empty book for %s" % symbol)
    try:
        bid = float(bids[0]["price"])
        ask = float(asks[0]["price"])
    except (TypeError, ValueError, KeyError, IndexError):
        raise FetchError("wallex: malformed book for %s" % symbol)
    # depth = Toman notional resting in the top 5 levels each side (a real liquidity proxy)
    def notional(levels):
        tot = 0.0
        for lv in levels[:5]:
            try:
                tot += float(lv.get("price")) * float(lv.get("quantity"))
            except (TypeError, ValueError, KeyError):
                continue
        return tot
    return {
        "value": (bid + ask) / 2.0,
        "src_ts": int(time.time()),          # venue does not stamp; we stamp on receipt
        "source": "wallex:%s" % symbol,
        "stale": False,
        "extra": {"bid": bid, "ask": ask, "spread": ask - bid,
                  "bid_depth_tmn": notional(bids), "ask_depth_tmn": notional(asks)},
    }


def bitpin(symbol: str = "BTC_IRT", timeout: int = 20) -> dict:
    """Fallback venue. Bitpin tickers carry last price + 24h volume."""
    d = get(BITPIN_TICKER, ua=UA_BROWSER, timeout=timeout)
    rows = d if isinstance(d, list) else (d or {}).get("results") or []
    for r in rows:
        if (r.get("symbol") or "").upper() == symbol.upper():
            px = r.get("price") or r.get("last")
            if px is None:
                break
            return {"value": float(px), "src_ts": None, "source": "bitpin:%s" % symbol,
                    "stale": False,
                    "extra": {"volume_24h": r.get("volume_24h"), "change": r.get("change")}}
    raise FetchError("bitpin: %s not found" % symbol)
