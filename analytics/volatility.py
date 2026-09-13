#!/usr/bin/env python3
"""volatility.py — USD vs USDT volatility, momentum and lead/lag study.

Answers two questions weekly:
  * which of the street dollar / digital dollar is more volatile?
  * which one carries more momentum (do its moves persist)?

Reads data/history.jsonl. Dry-run safe: prints the report and only sends to
the Telegram whitelist unless --no-send / --dry is passed.

  python3 analytics/volatility.py [--days N] [--dry]
"""
import statistics as st
import sys
import time
from datetime import timedelta

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from _stats import (acf1, corr, load_rows, log_rets, sd, send_telegram, stamp,
                    tehran, variance_ratio)


def hour_rets(rows, field):
    """Returns expressed as % per hour, so uneven cron gaps stay comparable."""
    out, times = [], []
    for i in range(len(rows) - 1):
        dt = (rows[i + 1]["ts"] - rows[i]["ts"]) / 3600.0
        a, b = rows[i].get(field), rows[i + 1].get(field)
        if dt <= 0 or not a or not b:
            continue
        out.append((b / a - 1.0) * 100.0 / dt)
        times.append(rows[i + 1]["ts"])
    return out, times


def main():
    args = sys.argv[1:]
    days = int(args[args.index("--days") + 1]) if "--days" in args else 30
    dry = "--dry" in args or "--no-send" in args

    rows = load_rows(days=days, fields=("usd", "usdt"))
    if len(rows) < 10:
        print(f"not enough history ({len(rows)} rows)")
        return

    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600.0
    usd = [r["usd"] for r in rows]
    usdt = [r["usdt"] for r in rows]
    r_u, r_t = log_rets(usd), log_rets(usdt)
    h_u, t_u = hour_rets(rows, "usd")
    h_t, _ = hour_rets(rows, "usdt")

    L = [f"📉 irx weekly — USD vs USDT (volatility & momentum)",
         f"{tehran(rows[0]['ts']):%d %b} → {tehran(rows[-1]['ts']):%d %b %Y}, "
         f"{len(rows)} snapshots / {span_h:.0f}h",
         "",
         "LEVELS",
         f"  USD   {min(usd):,.0f} – {max(usd):,.0f} T, last {usd[-1]:,.0f} ({100*(usd[-1]/usd[0]-1):+.2f}% over window)",
         f"  USDT  {min(usdt):,.0f} – {max(usdt):,.0f} T, last {usdt[-1]:,.0f} ({100*(usdt[-1]/usdt[0]-1):+.2f}% over window)",
         f"  USDT/USD premium: {(usdt[-1]/usd[-1]-1)*100:+.2f}% now",
         "",
         "VOLATILITY (higher = jumpier)",
         f"  per 30-min step, sd of log returns",
         f"    USD  {sd(r_u)*100:.4f}%   USDT {sd(r_t)*100:.4f}%",
         f"  hour-normalised, sd",
         f"    USD  {sd(h_u):.4f} %/h   USDT {sd(h_t):.4f} %/h",
         f"  typical move (mean |hour return|)",
         f"    USD  {st.mean(abs(x) for x in h_u):.4f} %/h   USDT {st.mean(abs(x) for x in h_t):.4f} %/h",
         "  largest single hour move",
         f"    USD  {max(h_u, key=abs):+.2f} %/h   USDT {max(h_t, key=abs):+.2f} %/h",
         "",
         "MOMENTUM (does a move keep going?)",
         f"  lag-1 autocorrelation   USD {acf1(r_u):+.3f}   USDT {acf1(r_t):+.3f}",
         "    (>0 = trending, ~0 = random, <0 = mean-reverting)",
         ]
    for name, r in (("USD", r_u), ("USDT", r_t)):
        if len(r) < 2:
            continue
        same = sum(1 for i in range(len(r) - 1) if r[i] * r[i + 1] > 0)
        tot = sum(1 for i in range(len(r) - 1) if r[i] * r[i + 1] != 0)
        if tot:
            L.append(f"  next move continues direction: {name} {100*same/tot:.1f}%")
    for name, v in (("USD", usd), ("USDT", usdt)):
        vr = variance_ratio(v)
        if vr is not None:
            L.append(f"  variance ratio VR(5): {name} {vr:.3f}")

    L += ["", "LEAD / LAG (who moves first?)",
          "  corr(USD return_t, USDT return_t+k):"]
    for k in (-2, -1, 0, 1, 2):
        if k >= 0:
            a, b = (r_u[k:], r_t[:len(r_t) - k] if k else r_t)
        else:
            a, b = (r_u[:len(r_u) + k], r_t[-k:])
        L.append(f"    k={k:+d}: {corr(a, b):+.3f}")
    L += ["    peak at k=0 means neither leads; the street price adjusts first.",
          "",
          "VERDICT"]

    vol_ratio = (sd(h_t) / sd(h_u)) if sd(h_u) else float("nan")
    mom_u, mom_t = acf1(r_u), acf1(r_t)
    L.append(f"  USDT is {vol_ratio:.2f}× as volatile as USD (hour-normalised sd).")
    if mom_u > mom_t:
        L.append(f"  USD is the trendier series (autocorr {mom_u:+.3f} vs {mom_t:+.3f}); "
                 "its moves persist, USDT's jitter mean-reverts.")
    else:
        L.append(f"  USDT is the trendier series (autocorr {mom_t:+.3f} vs {mom_u:+.3f}).")
    if len(rows) < 200:
        L.append(f"  ⚠ only {len(rows)} snapshots — directional, not yet significant.")
    L += ["", stamp(), "Informational only — not investment advice."]

    report = "\n".join(L)
    sent, failed = send_telegram(report, dry=dry)
    if not dry:
        print(f"sent to {sent} chat(s), {failed} failed")


if __name__ == "__main__":
    main()
