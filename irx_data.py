#!/usr/bin/env python3
"""irx_data.py — single source of truth for market data + derived fields.

Every consumer (brief, snapshot sampler, daily OHLC report) imports snapshot()
so the numbers are computed once, identically. Stdlib only.

snapshot() returns a dict with:
  raw prices      usd, usdt, cny, aed, eur, g18, g24, emami, quarter,
                  btc, eth, bnb, xrp, sol, xau_global
  derived         imp_*, gap_*, gold_gap, gap18, emami_bub, emami_bub_dom,
                  qtr_bub, qtr_bub_dom, tether_prem, theo_g
  provenance      src_time (BrsAPI's own quote time), xau_live
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import envcfg as E

TEHRAN = timezone(timedelta(hours=3, minutes=30))
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
      "Accept": "application/json, text/plain, */*"}
GRAM_PER_OZ = 31.1034768
COIN_GRAM, COIN_PURITY = 8.105, 0.900
K18 = 0.75
BRS_URL = "https://api.brsapi.ir/Market/Gold_Currency.php?key={}"
SQ_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
ERA_URL = "https://v6.exchangerate-api.com/v6/{}/latest/USD"


def get_json(url, timeout=25, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def pct(a, b):
    return (a / b - 1.0) * 100.0


def fetch_brs():
    """Tehran street quotes, flattened to {symbol: {price, chg, t}}."""
    d = get_json(BRS_URL.format(E.get("BRS_API_KEY")))
    out = {}
    for grp in ("gold", "currency", "cryptocurrency"):
        for it in d.get(grp, []):
            out[it["symbol"]] = {"price": float(it["price"]),
                                 "chg": it.get("change_percent", 0),
                                 "t": it.get("time_unix")}
    return out


def fetch_xau(brs):
    """Live world gold ounce; falls back to BrsAPI's (circular) quote."""
    try:
        sq = get_json(SQ_URL)
        mids = [(float(sp["bid"]) + float(sp["ask"])) / 2
                for src in sq for sp in src.get("spreadProfilePrices", [])]
        if mids:
            return round(sum(mids) / len(mids), 2), True
    except Exception as e:
        sys.stderr.write(f"[warn] Swissquote failed ({e})\n")
    return brs["XAUUSD"]["price"], False


def fetch_world_crosses():
    """Daily world crosses vs USD. None if unavailable."""
    try:
        e = get_json(ERA_URL.format(E.get("ERA_API_KEY")))
        return {c: e["conversion_rates"][c] for c in ("CNY", "AED", "EUR")}
    except Exception as ex:
        sys.stderr.write(f"[warn] ExchangeRate-API failed: {ex}\n")
        return None


def snapshot(with_world=True):
    """One complete market observation. Raises on BrsAPI failure (required)."""
    ts = int(time.time())
    brs = fetch_brs()
    xau, xau_live = fetch_xau(brs)
    era = fetch_world_crosses() if with_world else None

    px = lambda s: brs[s]["price"]
    ch = lambda s: brs[s]["chg"]

    row = {"ts": ts, "src_time": brs.get("USD", {}).get("t"),
           "usd": px("USD"), "usdt": px("USDT_IRT"), "aed": px("AED"),
           "cny": px("CNY"), "eur": px("EUR"),
           "g18": px("IR_GOLD_18K"), "g24": px("IR_GOLD_24K"),
           "emami": px("IR_COIN_EMAMI"), "quarter": px("IR_COIN_QUARTER"),
           "btc": px("BTC"), "eth": px("ETH"), "bnb": px("BNB"),
           "xrp": px("XRP"), "sol": px("SOL"),
           "xau_global": xau, "xau_live": xau_live,
           "brs_chg": {s: ch(s) for s in ("USD", "USDT_IRT", "AED", "CNY", "EUR",
                                          "IR_GOLD_18K", "IR_GOLD_24K",
                                          "IR_COIN_EMAMI", "IR_COIN_QUARTER",
                                          "BTC", "ETH", "BNB", "XRP", "SOL")}}

    imp = {k: row["usd"] / row[k] for k in ("aed", "cny", "eur")}
    row["imp_aed"], row["imp_cny"], row["imp_eur"] = (round(imp[k], 4) for k in imp)
    if era:
        row["gap_aed"] = round(pct(era["AED"], imp["aed"]), 3)
        row["gap_cny"] = round(pct(era["CNY"], imp["cny"]), 3)
        row["gap_eur"] = round(pct(era["EUR"], imp["eur"]), 3)
    else:
        row["gap_aed"] = row["gap_cny"] = row["gap_eur"] = None

    theo_g = xau * row["usd"] / GRAM_PER_OZ
    row["theo_g"] = round(theo_g)
    row["gold_gap"] = round(pct(row["g24"], theo_g), 3)
    row["gap18"] = round(pct(row["g18"], theo_g * K18), 3)
    melt_w_em = theo_g * COIN_GRAM * COIN_PURITY
    melt_w_q = theo_g * (COIN_GRAM / 4) * COIN_PURITY
    melt_d_em = row["g24"] * COIN_GRAM * COIN_PURITY
    melt_d_q = row["g24"] * (COIN_GRAM / 4) * COIN_PURITY
    row["emami_bub"] = round(pct(row["emami"], melt_w_em), 3)
    row["emami_bub_dom"] = round(pct(row["emami"], melt_d_em), 3)
    row["qtr_bub"] = round(pct(row["quarter"], melt_w_q), 3)
    row["qtr_bub_dom"] = round(pct(row["quarter"], melt_d_q), 3)
    row["tether_prem"] = round(pct(row["usdt"], row["usd"]), 3)
    return row


if __name__ == "__main__":
    r = snapshot()
    for k, v in r.items():
        if k != "brs_chg":
            print(f"  {k:15} {v}")
