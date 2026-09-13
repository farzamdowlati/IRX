#!/usr/bin/env python3
"""daily_insight.py — qualitative market-wide insight for the daily report.

The numbers already come from daily_report.py; this adds the narrative layer:
the Tehran-vs-world relationship, the domestic/international gap, and what to
watch. Delivered as its own Telegram message 15 minutes after the numbers.

Fails soft: any LLM error just delivers the deterministic summary instead.

  python3 analytics/daily_insight.py [--date YYYY-MM-DD] [--dry]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _stats import load_rows, send_telegram, stamp, tehran  # noqa: E402

import envcfg as E  # noqa: E402

LLM_UA = "irx-brief/4.0"
DAILY = os.path.join(E.DATA_DIR, "daily.jsonl")
GAPS = ("gap_aed", "gap_cny", "gap_eur", "gold_gap", "emami_bub", "tether_prem")


def load_daily(date_str=None):
    if not os.path.exists(DAILY):
        return None
    best = None
    for line in open(DAILY):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if date_str and r.get("date") != date_str:
            continue
        if best is None or r.get("date", "") > best.get("date", ""):
            best = r
    return best


def post_json(url, payload, timeout=45, headers=None):
    import urllib.request
    h = {"Content-Type": "application/json", "User-Agent": LLM_UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def gap_trend(date_str):
    """Intraday path of the Tehran-vs-world gaps, for the LLM to reason over."""
    rows = [r for r in load_rows(days=3) if r.get("gap_cny") is not None]
    day = [r for r in rows if tehran(r["ts"]).strftime("%Y-%m-%d") == date_str]
    step = max(1, len(day) // 8)
    return [{k: r.get(k) for k in ("ts",) + GAPS} for r in day[::step]]


def main():
    args = sys.argv[1:]
    dry = "--dry" in args or "--no-send" in args
    date_str = args[args.index("--date") + 1] if "--date" in args else None
    rec = load_daily(date_str)
    if not rec:
        print("no daily record yet — run daily_report.py first")
        return
    date_str = rec["date"]

    assets = {k: {"ohlc": v["ohlc"], "verdict": v["trend"]["verdict"],
                  "net": v["trend"]["net_pct"], "er": v["trend"]["er"]}
              for k, v in rec["assets"].items()}
    brief = {"date": date_str, "assets": assets, "gap_path": gap_trend(date_str)}

    body = None
    base, key, model = E.get("LLM_BASE_URL"), E.get("LLM_API_KEY"), E.get("LLM_MODEL")
    if base and key and model:
        try:
            prompt = (
                "You are writing the closing note for an Iran-vs-world market daily, for readers "
                "new to markets. JSON: assets = each asset's OHLC session result with a decided "
                "stance (Bullish/Bearish/Sideways) and efficiency ratio (er: 1 = clean trend, "
                "near 0 = choppy noise despite movement). gap_path = how Tehran-vs-world gaps "
                "evolved during the day — gap_aed/gap_cny/gap_eur: + means the foreign currency "
                "costs more in Tehran than worldwide; gold_gap: - means Iranian gold trades below "
                "world parity; emami_bub: coin premium over its gold content; tether_prem: USDT "
                "over USD on the street.\n"
                f"{json.dumps(brief)}\n"
                "Write 4-6 short plain sentences, no bullets, no headings, covering in this order: "
                "(1) what really moved today vs what only looked like it moved, using the er values; "
                "(2) how Tehran's prices relate to international ones right now and whether that "
                "relationship widened or narrowed during the session; "
                "(3) what it means for someone holding toman; (4) one specific thing to watch "
                "tomorrow. Quote at most 4 numbers. No advice, no disclaimers.")
            d = post_json(base.rstrip("/") + "/chat/completions",
                          {"model": model, "max_tokens": E.get_int("LLM_MAX_TOKENS", 700),
                           "temperature": 0.3, "reasoning_effort": "low",
                           "messages": [{"role": "user", "content": prompt}]},
                          headers={"Authorization": f"Bearer {key}"})
            body = (d["choices"][0]["message"].get("content") or "").strip()[:900] or None
        except Exception as e:
            sys.stderr.write(f"[warn] insight failed: {e}\n")
    if not body:
        counts = {}
        for a in assets.values():
            v = a["verdict"].split()[0]
            counts[v] = counts.get(v, 0) + 1
        body = ("Session: " + ", ".join(f"{n} {c}" for n, c in sorted(counts.items()))
                + ". (LLM insight unavailable today.)")

    text = "\n".join([
        f"🧭 Daily insight — {date_str}",
        "", body, "",
        stamp(), "Informational only — not investment advice."])
    sent, failed = send_telegram(text, dry=dry)
    if not dry:
        print(f"daily_insight {date_str}: sent to {sent} chat(s), {failed} failed")


if __name__ == "__main__":
    main()
