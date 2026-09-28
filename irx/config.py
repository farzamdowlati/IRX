"""irx/config.py — the single place where the series catalogue and source chains live.

Everything downstream (ingest, store, analysis, render) reads SERIES from here, so
adding a market means adding one entry, not touching five modules.
"""
from __future__ import annotations

import os
import sys
from typing import TypedDict

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import envcfg as E  # noqa: E402  (repo-root flat .env reader)

TEHRAN_OFFSET = 3.5          # hours, for display only; tz math uses zoneinfo
DB_PATH = os.environ.get("IRX_DB") or os.path.join(_REPO, "data", "irx.db")


def env(key: str, default: str = "") -> str:
    return E.get(key, default)


def env_int(key: str, default: int) -> int:
    try:
        return int(E.get(key, "") or default)
    except (TypeError, ValueError):
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(E.get(key, "") or default)
    except (TypeError, ValueError):
        return default


def env_bool(key: str, default: bool = False) -> bool:
    v = (E.get(key, "") or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


class Series(TypedDict, total=False):
    """One catalogue entry. Typed so every consumer sees str keys, not `object`."""
    id: str
    label: str
    group: str
    cadence: str
    session: str
    dp: int
    unit: str
    render: bool
    sources: list


# --------------------------------------------------------------------------
# Series catalogue
#
#   id       stable key used everywhere (DB, pages, alerts)
#   label    how it renders
#   group    world | iran | crypto
#   cadence  intl (5 min) | iran (15 min)
#   session  key into markets.SESSION_RULES
#   dp       decimal places to render
#   unit     free text shown in tooltips/debug
#   sources  ordered [(provider, symbol)] — index 0 is canonical, the rest are
#            fetched as cross-checks and stored under "<id>@<provider>"
#
# OPEC basket is deliberately absent: no keyless route exists (see docs/RESHAPE-PLAN.md §10.2).
# --------------------------------------------------------------------------
_WORLD: list[Series] = [
    # ---- FX -------------------------------------------------------------
    Series(id="EURUSD", label="EUR/USD", group="world", cadence="intl", session="fx", dp=4,
         unit="USD per EUR",
         sources=[("biquote", "EURUSD"), ("swissquote", "EUR/USD")]),
    Series(id="USDJPY", label="USD/JPY", group="world", cadence="intl", session="fx", dp=2,
         unit="JPY per USD",
         sources=[("biquote", "USDJPY"), ("swissquote", "USD/JPY")]),
    Series(id="USDCNY", label="USD/CNY", group="world", cadence="intl", session="fx", dp=4,
         unit="CNY per USD",
         sources=[("biquote", "USDCNY"), ("biquote", "USDCNH"), ("erapi", "CNY")]),
    Series(id="USDAED", label="USD/AED", group="world", cadence="intl", session="fx", dp=4,
         unit="AED per USD (peg 3.6725)",
         sources=[("biquote", "USDAED"), ("erapi", "AED")]),
    # ---- US equities / indices -----------------------------------------
    Series(id="NASDAQ", label="NASDAQ 100", group="world", cadence="intl", session="cash:US", dp=1,
         unit="index (CFD USTEC)",
         sources=[("biquote", "USTEC")]),
    Series(id="SP500", label="S&P 500", group="world", cadence="intl", session="cash:US", dp=1,
         unit="index (CFD US500)",
         sources=[("biquote", "US500")]),
    Series(id="DJI", label="Dow 30", group="world", cadence="intl", session="cash:US", dp=1,
         unit="index (CFD US30)",
         sources=[("biquote", "US30")]),
    # ---- Asia -----------------------------------------------------------
    Series(id="NIKKEI", label="Nikkei 225", group="world", cadence="intl", session="cash:JP", dp=1,
         unit="index (CFD JP225)",
         sources=[("biquote", "JP225")]),
    Series(id="HK", label="Hang Seng", group="world", cadence="intl", session="cash:HK", dp=1,
         unit="index (CFD HK50)",
         sources=[("biquote", "HK50")]),
    # ---- Europe / Oceania (extra coverage, cheap) ----------------------
    Series(id="DAX", label="DAX 40", group="world", cadence="intl", session="cash:DE", dp=1,
         unit="index (CFD DE30)",
         sources=[("biquote", "DE30")]),
    Series(id="FTSE", label="FTSE 100", group="world", cadence="intl", session="cash:UK", dp=1,
         unit="index (CFD UK100)",
         sources=[("biquote", "UK100")]),
    Series(id="ASX", label="ASX 200", group="world", cadence="intl", session="cash:AU", dp=1,
         unit="index (CFD AUS200)",
         sources=[("biquote", "AUS200")]),
    # ---- Energy --------------------------------------------------------
    Series(id="WTI", label="WTI crude", group="world", cadence="intl", session="futures", dp=2,
         unit="USD/bbl (CFD USOIL)",
         sources=[("biquote", "USOIL")]),
    Series(id="BRENT", label="Brent crude", group="world", cadence="intl", session="futures", dp=2,
         unit="USD/bbl (CFD UKOIL)",
         sources=[("biquote", "UKOIL")]),
    # ---- Metals --------------------------------------------------------
    Series(id="XAUUSD", label="Gold spot", group="world", cadence="intl", session="futures", dp=2,
         unit="USD/oz",
         sources=[("biquote", "XAUUSD"), ("swissquote", "XAU/USD")]),
    Series(id="XAGUSD", label="Silver spot", group="world", cadence="intl", session="futures", dp=3,
         unit="USD/oz",
         sources=[("biquote", "XAGUSD"), ("swissquote", "XAG/USD")]),
    # ---- Dollar index --------------------------------------------------
    Series(id="DXY", label="Dollar index", group="world", cadence="intl", session="fx", dp=2,
           unit="ICE index (DXY)",
           sources=[("biquote", "DXY")]),
    # ---- Crypto (24/7) -------------------------------------------------
    Series(id="BTCUSD", label="Bitcoin", group="world", cadence="intl", session="crypto", dp=0,
         unit="USD (24/7)",
         sources=[("biquote", "BTCUSD"), ("coingecko", "bitcoin")]),
]

_IRAN: list[Series] = [
    Series(id="USDIRT", label="Street USD", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per USD",
         sources=[("brsapi", "USD")]),
    Series(id="USDTIRT", label="USDT", group="iran", cadence="iran", session="crypto",
           dp=0, unit="Toman per USDT (24/7)",
           sources=[("brsapi", "USDT_IRT"), ("wallex", "USDTTMN")]),
    Series(id="G18", label="Gold 18k", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per gram",
         sources=[("brsapi", "IR_GOLD_18K")]),
    Series(id="G_ABSHODE", label="Melted gold", group="iran", cadence="iran",
           session="iran", dp=0, unit="Toman per mesghal (4.6083 g) of ~16.9k gold",
           sources=[("brsapi", "IR_GOLD_MELTED")]),
    Series(id="EMAMI", label="Emami coin", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per coin",
         sources=[("brsapi", "IR_COIN_EMAMI")]),
    Series(id="AEDIRT", label="Dirham", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per AED",
         sources=[("brsapi", "AED")]),
    Series(id="CNYIRT", label="Yuan", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per CNY",
         sources=[("brsapi", "CNY")]),
    Series(id="JPYIRT", label="Yen (per 100)", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per 100 JPY  <-- divide by 100 for any cross math",
         sources=[("brsapi", "JPY")]),
    Series(id="EURIRT", label="Euro", group="iran", cadence="iran", session="iran", dp=0,
         unit="Toman per EUR",
         sources=[("brsapi", "EUR")]),
    # NOTE: IR_GOLD_24K is deliberately absent — BrsAPI derives it from 18k
    # (g24 == g18 / 0.75), so it carries no independent information. Pure-gold
    # gram price is computed as g18/0.75 wherever melt value is needed.
]

# ---------------------------------------------------------------------------
# Derived / analysis-only series (not rendered as prices): the extra legs that
# make the dollar triangulation possible. BrsAPI's crypto is priced in USD, so a
# Toman BTC leg must come from a local venue (see sources/wallex.py).
_DERIVED: list[Series] = [
    Series(id="BTCIRT", label="Bitcoin (Toman)", group="iran", cadence="intl", session="crypto",
         dp=0, unit="Toman per BTC (24/7) - Wallex order book", render=False,
         sources=[("wallex", "BTCTMN")]),
]

SERIES = _WORLD + _IRAN + _DERIVED
BY_ID = {s["id"]: s for s in SERIES}

WORLD_IDS = [s["id"] for s in _WORLD]
IRAN_IDS = [s["id"] for s in _IRAN]
INTL_IDS = [s["id"] for s in SERIES if s["cadence"] == "intl"]
IRAN_CADENCE_IDS = [s["id"] for s in SERIES if s["cadence"] == "iran"]

# series that must render (everything the user asked to see)
RENDER_IDS = [s["id"] for s in SERIES if s.get("render", True)]

# groups as they appear on the Prices page
PAGE_GROUPS = [
    ("world", "World"),
    ("iran", "Tehran street"),
]

# ---------------------------------------------------------------- alerts
ALERT_MOVE_PCT = {
    "fx": env_float("ALERT_MOVE_FX_PCT", 2.0),
    "futures": env_float("ALERT_MOVE_FUTURES_PCT", 2.0),
    "crypto": env_float("ALERT_MOVE_CRYPTO_PCT", 5.0),
    "iran": env_float("ALERT_MOVE_IRAN_PCT", 1.5),
}
ALERT_INDEX_PCT = env_float("ALERT_MOVE_INDEX_PCT", 3.0)
ALERT_COOLDOWN_MIN = env_int("ALERT_COOLDOWN_MIN", 60)
ALERT_Z = env_float("ALERT_Z", 3.0)

# ---------------------------------------------------------------- cadence
INTL_INTERVAL_MIN = env_int("INTL_INTERVAL_MIN", 5)
IRAN_INTERVAL_MIN = env_int("IRAN_INTERVAL_MIN", 15)
REPORT_INTERVAL_MIN = env_int("REPORT_INTERVAL_MIN", 60)
FETCH_CONCURRENCY = env_int("FETCH_CONCURRENCY", 6)
FETCH_TIMEOUT = env_int("FETCH_TIMEOUT", 30)

# ---------------------------------------------------------------- bot
BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
OWNER_CHAT_ID = env_int("OWNER_CHAT_ID", 0)
BRS_KEY = env("BRS_API_KEY")
ERA_KEY = env("ERA_API_KEY")
UA_BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
UA_HONEST = "irx/2.0 (+personal market watchlist)"
