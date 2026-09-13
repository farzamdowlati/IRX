#!/usr/bin/env python3
"""daily_report.py — end-of-session OHLC + trend verdict, with a daily store.

Builds Open/High/Low/Close per asset for the 07:00-22:00 Tehran session from
sampled snapshots, appends one row per day to data/daily.jsonl (so the day is
kept forever), classifies each asset Bullish / Bearish / Sideways with the
deterministic engine in trend.py, and delivers the report to Telegram.

  python3 analytics/daily_report.py [--date YYYY-MM-DD] [--days N]
                                    [--dry] [--no-store]
"""
import json
import os
import statistics as st
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _stats import (TEHRAN, load_rows, send_telegram, stamp, tehran)  # noqa: E402
from trend import NOISE_PCT, classify, label  # noqa: E402

import envcfg as E  # noqa: E402

SAMPLES = os.path.join(E.DATA_DIR, "samples.jsonl")
DAILY = os.path.join(E.DATA_DIR, "daily.jsonl")
SESSION_START, SESSION_END = 7, 22

# report order and display names
ASSETS = [
    ("usd", "USD (street)", "T"),
    ("usdt", "USDT", "T"),
    ("aed", "AED", "T"),
    ("cny", "CNY", "T"),
    ("eur", "EUR", "T"),
    ("g24", "Gold 24k (gram)", "T"),
    ("g18", "Gold 18k (gram)", "T"),
    ("emami", "Emami coin", "T"),
    ("quarter", "Quarter coin", "T"),
    ("xau_global", "Gold ounce (world)", "$"),
    ("btc", "BTC", "$"),
    ("eth", "ETH", "$"),
    ("bnb", "BNB", "$"),
    ("xrp", "XRP", "$"),
    ("sol", "SOL", "$"),
]


def money(v, unit="$"):
    if unit == "T":
        return f"{v/1e6:.2f}M" if v >= 1e6 else f"{v:,.0f}"
    return f"{v:,.4f}".rstrip("0").rstrip(".") if v < 10 else f"{v:,.2f}"


def parse_ts():
    a = sys.argv[1:]
    if "--date" in a:
        d = datetime.strptime(a[a.index("--date") + 1], "%Y-%m-%d")
        return d.date()
    return datetime.now(TEHRAN).date()


def session_rows(day):
    """Sampled rows inside the 07:00-22:00 window for one Tehran date."""
    if not os.path.exists(SAMPLES):
        return []
    out = []
    for line in open(SAMPLES):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = tehran(r["ts"])
        if t.date() == day and SESSION_START <= t.hour <= SESSION_END:
            out.append(r)
    out.sort(key=lambda r: r["ts"])
    return out


def ohlc(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        return None
    return {"o": v[0], "h": max(v), "l": min(v), "c": v[-1], "n": len(v)}


def build(day):
    rows = session_rows(day)
    if not rows:
        return None
    out = {"date": day.isoformat(), "samples": len(rows),
           "first": rows[0]["ts"], "last": rows[-1]["ts"], "assets": {}}
    for key, _, unit in ASSETS:
        bars = ohlc(rows, key)
        if not bars:
            continue
        prices = [r[key] for r in rows if r.get(key) is not None]
        tr = classify(prices, NOISE_PCT.get(key))
        out["assets"][key] = {"ohlc": bars, "trend": tr, "unit": unit}
    return out


def store(rec, dry=False):
    """Append/replace the day's record in data/daily.jsonl."""
    if dry:
        return
    keep = []
    if os.path.exists(DAILY):
        for line in open(DAILY):
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("date") != rec["date"]:
                    keep.append(line)
            except ValueError:
                pass
    keep.append(json.dumps(rec))
    with open(DAILY, "w") as fp:
        fp.write("\n".join(keep) + "\n")


def render(rec):
    d = datetime.fromisoformat(rec["date"])
    L = [f"📅 Daily market report — {d:%a %d %b %Y}, session 07:00–22:00 Tehran",
         f"{rec['samples']} samples · "
         f"{tehran(rec['first']):%H:%M}–{tehran(rec['last']):%H:%M}",
         "",
         "OHLC (O / H / L / C) and stance:",
         "🟢 Bullish  🔴 Bearish  ⚪ Sideways — ▲▲▲ strength",
         ""]
    def num(v, fmt="{:+.2f}", dash="n/a"):
        return dash if v is None else fmt.format(v)

    for key, name, unit in ASSETS:
        a = rec["assets"].get(key)
        if not a:
            continue
        o = a["ohlc"]
        tr = a["trend"]
        if tr["verdict"] == "Insufficient data":
            continue
        u = "" if unit == "$" else "T"
        L.append(f"{name}")
        L.append(f"  O {money(o['o'], unit)}{u}  H {money(o['h'], unit)}{u}  "
                 f"L {money(o['l'], unit)}{u}  C {money(o['c'], unit)}{u}")
        L.append(f"  {label(tr['verdict'], tr['bars'])}  "
                 f"net {num(tr['net_pct'])}%  ER {tr['er']}  "
                 f"t {num(tr['slope_t'])}  R² {tr['r2']}")
    # market-wide summary
    counts = {"Bullish": 0, "Bearish": 0, "Sideways": 0}
    for key, _, _ in ASSETS:
        v = rec["assets"].get(key, {}).get("trend", {}).get("verdict")
        for k in counts:
            if v and v.startswith(k):
                counts[k] += 1
    L += ["", f"Session overall: {counts['Bullish']} bullish · "
              f"{counts['Bearish']} bearish · {counts['Sideways']} sideways",
          "", "How to read it:",
          "  ER (efficiency ratio) = net move ÷ total distance travelled.",
          "  Below 0.30 the price went nowhere despite moving a lot — that is the",
          "  'meaningless movement' case, reported as Sideways.",
          "  t is the trend slope measured in units of the asset's own noise:",
          "  below 1.0 the drift is inside normal wiggle and cannot be called a trend.",
          "", stamp(), "Informational only — not investment advice."]
    return "\n".join(L)


def main():
    args = sys.argv[1:]
    dry = "--dry" in args or "--no-send" in args
    day = parse_ts()
    rec = build(day)
    if not rec:
        print(f"no samples for {day} (looked in {SAMPLES})")
        return
    if "--no-store" not in args:
        store(rec, dry=dry)
    report = render(rec)
    sent, failed = send_telegram(report, dry=dry)
    if not dry:
        print(f"daily_report {day}: sent to {sent} chat(s), {failed} failed")


if __name__ == "__main__":
    main()
