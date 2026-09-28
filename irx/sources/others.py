"""Cross-check and fallback sources: Swissquote spot, CoinGecko, exchangerate-api."""
from __future__ import annotations

import time

from .. import config as C
from ..net import UA_HONEST, FetchError, get  # type: ignore

# ---------------------------------------------------------------- swissquote
SQ = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{}"


def sq_quote(symbol: str, timeout: int = 25) -> dict:
    """Spot metal/FX mid, averaged over the feed's spread profiles. Tight and real-time."""
    d = get(SQ.format(symbol.replace("/", "%2F")), ua=UA_HONEST, timeout=timeout)
    if not isinstance(d, list) or not d:
        raise FetchError("swissquote: no data for %s" % symbol)
    mids = []
    newest = 0
    for src in d:
        for sp in src.get("spreadProfilePrices", []) or []:
            try:
                mids.append((float(sp["bid"]) + float(sp["ask"])) / 2.0)
            except (TypeError, ValueError, KeyError):
                continue
        ts = src.get("ts")
        if isinstance(ts, (int, float)) and ts > newest:
            newest = int(ts)
    if not mids:
        raise FetchError("swissquote: empty book for %s" % symbol)
    return {"value": sum(mids) / len(mids),
            "src_ts": newest or None,
            "source": "swissquote:%s" % symbol,
            "stale": False,
            "extra": {"feeds": len(d), "profiles": len(mids)}}


# ----------------------------------------------------------------- coingecko
CG = "https://api.coingecko.com/api/v3/simple/price?ids={}&vs_currencies=usd"


def cg_quote(symbol: str, timeout: int = 25) -> dict:
    d = get(CG.format(symbol), ua=UA_HONEST, timeout=timeout)
    try:
        px = float(d[symbol]["usd"])
    except (TypeError, KeyError, ValueError):
        raise FetchError("coingecko: no price for %s" % symbol)
    return {"value": px, "src_ts": None, "source": "coingecko", "stale": False, "extra": {}}


# --------------------------------------------------- exchangerate-api (daily)
ERA_KEYED = "https://v6.exchangerate-api.com/v6/{key}/latest/USD"
ERA_OPEN = "https://open.er-api.com/v6/latest/USD"
_cache: dict = {"t": 0.0, "rates": None}


def era_rates(force: bool = False, timeout: int = 25) -> dict:
    if not force and _cache["rates"] and (time.time() - _cache["t"]) < 3600:
        return _cache["rates"]
    if C.ERA_KEY:
        d = get(ERA_KEYED.format(key=C.ERA_KEY), ua=UA_HONEST, timeout=timeout)
        rates = d.get("conversion_rates") or {}
        ts = d.get("time_last_update_unix")
    else:
        d = get(ERA_OPEN, ua=UA_HONEST, timeout=timeout)
        rates = d.get("rates") or {}
        ts = d.get("time_last_update_unix")
    if not rates:
        raise FetchError("exchangerate-api: empty rates")
    _cache.update({"t": time.time(), "rates": rates, "ts": ts})
    return rates


def era_quote(symbol: str, timeout: int = 25) -> dict:
    rates = era_rates(timeout=timeout)
    if symbol not in rates:
        raise FetchError("exchangerate-api: no %s" % symbol)
    return {"value": float(rates[symbol]), "src_ts": _cache.get("ts"),
            "source": "erapi:daily", "stale": True, "extra": {}}


# ------------------------------------------------------------- registry glue
PROVIDERS = {
    "biquote": None,       # imported lazily below (module name clashes with the package dir)
    "brsapi": None,
    "wallex": None,
    "bitpin": None,
    "swissquote": sq_quote,
    "coingecko": cg_quote,
    "erapi": era_quote,
}


def load_providers(fresh: bool = False):
    """Import the bulky providers lazily so a missing one cannot break the rest.

    Only fills slots that are empty (unless `fresh=True`): an injected function —
    a test stub, or an override during an outage — must survive, otherwise every
    fetch_series() call silently resets the registry back to the live providers.
    """
    from . import biquote, brsapi, wallex  # type: ignore
    defaults = {"biquote": biquote.quote, "brsapi": brsapi.quote,
                "wallex": wallex.quote, "bitpin": wallex.bitpin}
    for k, v in defaults.items():
        if fresh or PROVIDERS.get(k) is None:
            PROVIDERS[k] = v
    return PROVIDERS
