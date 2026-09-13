#!/usr/bin/env python3
"""fair_value.py — is street USD cheap against the rest of the market?

Each anchor (CNY, AED, EUR, gold) implies a "fair" street USD: the rate that
would make the Tehran-vs-world gap exactly zero. Comparing that to the actual
street USD gives a single, comparable measure of how far the dollar is
mispriced, per anchor and as a composite.

Reads data/history.jsonl. Dry-run safe (see --dry).

  python3 analytics/fair_value.py [--days N] [--dry]
"""
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _stats import GRAM_PER_OZ, corr, load_rows, send_telegram, stamp, tehran

ANCHORS = (("CNY", "cny"), ("AED", "aed"), ("EUR", "eur"))


def fair_usd(row):
    """Toman value each anchor says USD should trade at."""
    out = {}
    for name, key in ANCHORS:
        imp, gap, px = row.get("imp_" + key), row.get("gap_" + key), row.get(key)
        if imp is None or gap is None or not px:
            continue
        world_cross = imp * (1 + gap / 100.0)      # units of X per USD, worldwide
        out[name] = world_cross * px
    if row.get("g24") and row.get("xau_global"):
        # world parity gram (Toman) x coin-free gram -> fair USD from gold
        out["XAU"] = row["g24"] * GRAM_PER_OZ / row["xau_global"]
    return out


def cheapness(rows, anchor):
    """% by which street USD sits below the anchor's fair value."""
    out = []
    for r in rows:
        f = fair_usd(r)
        if anchor in f and r.get("usd"):
            out.append((r["ts"], (f[anchor] / r["usd"] - 1) * 100.0))
    return out


def main():
    args = sys.argv[1:]
    days = int(args[args.index("--days") + 1]) if "--days" in args else 30
    dry = "--dry" in args or "--no-send" in args

    rows = load_rows(days=days, fields=("usd", "gap_cny", "imp_cny", "cny"))
    if len(rows) < 5:
        print(f"not enough history ({len(rows)} rows)")
        return

    names_all = [n for n, _ in ANCHORS] + ["XAU"]
    series = {name: cheapness(rows, name) for name in names_all}
    series = {k: v for k, v in series.items() if v}

    L = ["📊 irx weekly — is the street USD cheap?",
         f"{tehran(rows[0]['ts']):%d %b} → {tehran(rows[-1]['ts']):%d %b %Y}, {len(rows)} snapshots",
         "",
         "For each anchor: how far street USD sits BELOW the rate that anchor",
         "implies. Positive = dollar is cheap, i.e. should be higher to hit parity.",
         ""]
    for name in ("CNY", "AED", "EUR", "XAU"):
        s = series.get(name)
        if not s:
            continue
        v = [x for _, x in s]
        L.append(f"  {name:4} mean {st.mean(v):+.2f}%  sd {st.pstdev(v):.2f}  "
                 f"range {min(v):+.2f}..{max(v):+.2f}  now {v[-1]:+.2f}%  (n={len(v)})")

    # composite over FX anchors only; gold carries its own domestic premium
    by_ts = {k: dict(v) for k, v in series.items()}
    fx = [n for n, _ in ANCHORS if n in series]
    comp = [(r["ts"], st.mean([by_ts[n][r["ts"]] for n in fx]))
            for r in rows if all(r["ts"] in by_ts[n] for n in fx)]
    if comp:
        v = [x for _, x in comp]
        L += ["", f"FX composite (mean of {'+'.join(fx)}, n={len(v)}):",
              f"  mean {st.mean(v):+.2f}%  sd {st.pstdev(v):.2f}  "
              f"range {min(v):+.2f}..{max(v):+.2f}  now {v[-1]:+.2f}%",
              f"  → street USD would need {v[-1]:+.2f}% to sit at FX consensus."]

    L += ["", "Do the anchors agree, or is each pair its own market?",
          "  (correlation of the cheapness series; ~1 = one common dollar story)"]
    names = [n for n in ("CNY", "AED", "EUR", "XAU") if n in series]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = dict(series[names[i]])
            b = dict(series[names[j]])
            ts = sorted(set(a) & set(b))
            if len(ts) > 2:
                L.append(f"  corr({names[i]:4},{names[j]:4}) = "
                         f"{corr([a[t] for t in ts], [b[t] for t in ts]):+.2f}")

    L += ["", "Trend by day (last 10):"]
    days_seen = {}
    for r in rows:
        days_seen.setdefault(tehran(r["ts"]).strftime("%a %d %b"), []).append(r)
    for d, rs in list(days_seen.items())[-10:]:
        cells = []
        for n in names:
            vals = [((fair_usd(r)[n] / r["usd"] - 1) * 100.0)
                    for r in rs if n in fair_usd(r) and r.get("usd")]
            if vals:
                cells.append(f"{n} {st.mean(vals):+.2f}")
        L.append(f"  {d}: USD {rs[-1]['usd']:,.0f}  " + "  ".join(cells))

    L += ["", "Where each anchor says USD should be right now:"]
    last = rows[-1]
    f = fair_usd(last)
    L.append(f"  actual {last['usd']:,.0f} T")
    for n in names:
        if n in f:
            L.append(f"  {n:4} → {f[n]:>10,.0f} T ({f[n]/last['usd']-1:+.2%})")

    L += ["", "Read:", 
          "  * Every anchor agreeing on one sign means the dollar itself is the",
          "    common factor; XY pair offsets that ignore each other do not.",
          "  * A constant offset on one pair (e.g. CNY ~+1.3% for weeks) is more",
          "    likely quote basis / spread than a tradable mispricing.",
          "  * Gold's discount is a domestic-market premium, not an FX signal.",
          "", stamp(), "Informational only — not investment advice."]

    report = "\n".join(L)
    sent, failed = send_telegram(report, dry=dry)
    if not dry:
        print(f"sent to {sent} chat(s), {failed} failed")


if __name__ == "__main__":
    main()
