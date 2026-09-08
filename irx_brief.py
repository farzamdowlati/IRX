#!/usr/bin/env python3
"""
irx_brief.py — Iran-vs-world market brief. Beginner-friendly, with optional
LLM insight. Stdlib only; no pip installs.

Sources (all free tiers):
  * BrsAPI        — Tehran street rates in Toman (needs a browser User-Agent;
                    their firewall bans python's default UA).
  * Swissquote    — live global XAU/USD mid, keyless.
  * exchangerate-api — daily world USD crosses (CNY, AED) + IRR estimate.

Computes gaps between Tehran-implied cross rates and world rates, gold parity,
coin bubbles; appends to data/history.jsonl; delivers to Telegram (whitelist).

Config lives in .env next to this file — see .env.example.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

import envcfg as E

TEHRAN = timezone(timedelta(hours=3, minutes=30))
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
      "Accept": "application/json, text/plain, */*"}
# NOTE: do NOT reuse the browser UA for OpenAI-compatible endpoints — Cloudflare
# (e.g. Groq) gives error 1010 for spoofed Chrome strings but accepts honest clients.
LLM_UA = "irx-brief/3.0"
GRAM_PER_OZ = 31.1034768
COIN_GRAM, COIN_PURITY = 8.105, 0.900   # full Bahar Azadi / Emami spec


def get_json(url, timeout=25, headers=None):
    h = dict(UA)
    if headers: h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_json(url, payload, timeout=40, headers=None):
    h = {"Content-Type": "application/json", "User-Agent": LLM_UA}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def in_window(now, start, end):
    """Tehran wall-clock window, end inclusive to the hour."""
    hm = now.hour * 60 + now.minute
    return start * 60 <= hm <= end * 60


def load_history():
    p = os.path.join(E.DATA_DIR, "history.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def load_whitelist():
    p = os.path.join(E.DATA_DIR, "whitelist.json")
    try:
        return sorted(json.load(open(p)))
    except Exception:
        owner = E.get_int("OWNER_CHAT_ID", 0)
        return [owner] if owner else []


def tg_send(cfg_chat_id, token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data, headers=UA, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(f"telegram: {resp}")


def llm_insight(brief, hist_rows):
    """Optional plain-English read from any OpenAI-compatible endpoint.
    Fails soft: any error just returns None and the brief sends without it."""
    base, key, model = E.get("LLM_BASE_URL"), E.get("LLM_API_KEY"), E.get("LLM_MODEL")
    if not (base and key and model):
        return None
    try:
        trend = [{
            "t": time.strftime("%H:%M", time.gmtime(r["ts"] + 3.5 * 3600)),
            "usd": round(r["usd"]), "gap_cny": r.get("gap_cny"), "gap_aed": r.get("gap_aed"),
            "gold_gap": r.get("gold_gap"), "emami_bub": r.get("emami_bub"), "xau": r.get("xau_global"),
        } for r in hist_rows[-int(E.get("INSIGHT_TREND_N", "12")):]]
        prompt = (
            "You are writing for Iranians new to markets. Given JSON of Tehran-vs-world price gaps "
            "(percent: + means that asset is priced richer through Tehran street rates than world cross-rates imply; "
            "gold_gap - means Iranian gold is cheaper than world parity; emami_bub = coin premium over its gold content) "
            "and an intraday trend (Tehran local times):\n"
            f"NOW: {json.dumps(brief)}\nTREND: {json.dumps(trend)}\n"
            "Write EXACTLY 'Insight:' + 2-3 short plain-English sentences: what moved since earlier today, "
            "whether any gap looks abnormal vs its own intraday range, one thing to watch next. "
            "No extra number restatement beyond 1-2 percentages, no advice, no bullets, max 60 words.")
        d = post_json(base.rstrip("/") + "/chat/completions",
                      {"model": model, "max_tokens": E.get_int("LLM_MAX_TOKENS", 500),
                       "temperature": 0.3, "reasoning_effort": "low",
                       "messages": [{"role": "user", "content": prompt}]},
                      headers={"Authorization": f"Bearer {key}"})
        c = (d["choices"][0]["message"].get("content") or "").strip()
        return c[:400] or None
    except Exception as e:
        sys.stderr.write(f"[warn] insight failed: {e}\n")
        return None


def fmt(v):
    return f"{v:+.2f}%" if v is not None else "n/a"


def main():
    force = "--force" in sys.argv
    os.makedirs(E.DATA_DIR, exist_ok=True)
    now = datetime.now(TEHRAN)
    if not force and not in_window(now, E.get_int("WINDOW_START", 7), E.get_int("WINDOW_END", 22)):
        sys.exit(0)
    ts = int(time.time())

    # ---------- fetch: Tehran street ----------
    d = get_json("https://api.brsapi.ir/Market/Gold_Currency.php?key={}".format(E.get("BRS_API_KEY")))
    brs = {}
    for grp in ("gold", "currency", "cryptocurrency"):
        for it in d.get(grp, []):
            brs[it["symbol"]] = {"price": float(it["price"]), "chg": it.get("change_percent", 0)}

    # ---------- fetch: world XAU (live) ----------
    try:
        sq = get_json("https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD")
        mids = [(float(sp["bid"]) + float(sp["ask"])) / 2
                for src in sq for sp in src.get("spreadProfilePrices", [])]
        xau, xau_live = round(sum(mids) / len(mids), 2), True
    except Exception as e:
        sys.stderr.write(f"[warn] Swissquote failed ({e}); using BrsAPI XAUUSD (stale/circular)\n")
        xau, xau_live = brs["XAUUSD"]["price"], False

    # ---------- fetch: world crosses (daily) ----------
    era = None
    try:
        e = get_json("https://v6.exchangerate-api.com/v6/{}/latest/USD".format(E.get("ERA_API_KEY")))
        era = {"CNY": e["conversion_rates"]["CNY"], "AED": e["conversion_rates"]["AED"],
               "IRR": e["conversion_rates"]["IRR"]}
    except Exception as ex:
        sys.stderr.write(f"[warn] ExchangeRate-API failed: {ex}\n")

    # ---------- compute ----------
    usd, usdt = brs["USD"]["price"], brs["USDT_IRT"]["price"]
    cny, aed = brs["CNY"]["price"], brs["AED"]["price"]
    g24, emami, quarter = (brs["IR_GOLD_24K"]["price"], brs["IR_COIN_EMAMI"]["price"],
                           brs["IR_COIN_QUARTER"]["price"])
    imp_cny, imp_aed = usd / cny, usd / aed
    theo_g = xau * usd / GRAM_PER_OZ
    coin_melt, qtr_melt = theo_g * COIN_GRAM * COIN_PURITY, theo_g * (COIN_GRAM / 4) * COIN_PURITY
    pct = lambda a, b: (a / b - 1.0) * 100.0
    gap_cny = pct(era["CNY"], imp_cny) if era else None
    gap_aed = pct(era["AED"], imp_aed) if era else None
    gold_gap = pct(g24, theo_g)
    emami_bub = pct(emami, coin_melt)
    qtr_bub = pct(quarter, qtr_melt)
    tether_prem = pct(usdt, usd)
    off = era["IRR"] / 10.0 if era else None          # IRR -> Toman
    off_ratio = usd / off if off else None

    hist = load_history()
    prev = hist[-1] if hist else None
    day_rows = [r for r in hist if time.strftime("%j", time.gmtime(r["ts"] + 3.5 * 3600))
                == time.strftime("%j", time.gmtime(ts + 3.5 * 3600))]
    usd_lo = min((r["usd"] for r in day_rows), default=usd)
    usd_hi = max((r["usd"] for r in day_rows), default=usd)

    brief = {"usd": round(usd), "gap_cny": None if gap_cny is None else round(gap_cny, 3),
             "gap_aed": None if gap_aed is None else round(gap_aed, 3),
             "gold_gap": round(gold_gap, 3), "emami_bub": round(emami_bub, 3),
             "qtr_bub": round(qtr_bub, 3), "tether_prem": round(tether_prem, 3),
             "xau_global": xau, "off_ratio": round(off_ratio, 3) if off_ratio else None}

    # ---------- compose beginner-friendly text ----------
    interval_min = E.get_int("INTERVAL_MINUTES", 30)
    L = [f"📊 Iran Market Brief — {now:%a %d %b, %H:%M} Tehran",
         "Street prices compared with world markets. Toman = what you'd actually pay in Tehran.",
         "Updates every " + str(interval_min) + " min, 07:00–22:00.",
         "",
         f"💵 US dollar on the street: {usd:,.0f} T (today {'+' if brs['USD']['chg'] >= 0 else ''}{brs['USD']['chg']}%)",
         f"   USDT (digital dollar): {usdt:,.0f} T → 0% means both move together (now {fmt(tether_prem)})."]
    if off:
        L.append(f"   Street vs lagged world-rate ref ({off:,.0f} T): {off_ratio:.2f}× — how far reality sits from the reference rate.")
    L.append(f"   Today's street range so far: {usd_lo:,.0f} – {usd_hi:,.0f} T"
             + (f" · moved {usd - prev['usd']:+,.0f} T since last brief" if prev else ""))
    L.append("")
    if era:
        L.append("🔀 Cross-market check — if these drift from ~0, a currency pair is priced differently in Tehran than worldwide:")
        L.append(f"   Yuan: {fmt(gap_cny)}  [world {era['CNY']:.3f} vs Tehran-implied {imp_cny:.3f}]"
                 + (f" · Δ{gap_cny - prev['gap_cny']:+.2f}pp" if prev and prev.get("gap_cny") is not None else ""))
        L.append(f"   Dirham: {fmt(gap_aed)}  [world {era['AED']:.4f} vs {imp_aed:.4f}]"
                 + (f" · Δ{gap_aed - prev['gap_aed']:+.2f}pp" if prev and prev.get("gap_aed") is not None else ""))
        L.append("   + means the dollar is bought a bit cheap in Tehran against that currency (yuan/dirham cost more there). Under ±1% is normal noise.")
        L.append("")
    L.append(f"🥇 Gold: world ounce ${xau:,.2f} ({'live world quote' if xau_live else 'BrsAPI quote (may be stale)'}); Tehran gram {g24/1e6:.2f}M T.")
    L.append(f"   Theoretical gram from world price: {theo_g/1e6:.2f}M → Tehran sits {fmt(gold_gap)} off parity"
             + (f" (Δ{gold_gap - prev['gold_gap']:+.2f}pp)" if prev and prev.get("gold_gap") is not None else "") + ".")
    L.append(f"   Emami coin: {emami/1e6:.1f}M vs its gold worth {coin_melt/1e6:.1f}M → bubble {fmt(emami_bub)}")
    L.append(f"   Quarter coin: bubble {fmt(qtr_bub)} — small coins always carry a big minting premium; the number to watch is how fast it changes.")
    ins = llm_insight(brief, hist)
    if ins:
        L += ["", ins if ins.startswith("Insight") else f"Insight: {ins}"]
    alert = [f"{n} {fmt(v)} past {thr}%" for k, n, thr in
             (("gap_cny", "yuan gap", E.get_float("ALERT_GAP_PCT", 2.0)),
              ("gap_aed", "dirham gap", E.get_float("ALERT_GAP_PCT", 2.0)),
              ("gold_gap", "gold vs world", E.get_float("ALERT_GOLD_PCT", 3.0)),
              ("emami_bub", "coin bubble", E.get_float("ALERT_COIN_PCT", 8.0)))
             if (v := brief[k]) is not None and abs(v) >= thr]
    if alert:
        L += ["", "⚠ " + "; ".join(alert)]
    L += ["", "Not investment advice — informational only."]

    text = "\n".join(L)
    print(text)

    # ---------- persist ----------
    snap = dict(brief, ts=ts, g24=g24, emami=emami, quarter=quarter, cny=cny, aed=aed,
                usdt=usdt, imp_cny=round(imp_cny, 4), imp_aed=round(imp_aed, 4), insight=ins)
    with open(os.path.join(E.DATA_DIR, "history.jsonl"), "a") as fp:
        fp.write(json.dumps(snap) + "\n")
    with open(os.path.join(E.DATA_DIR, "latest.json"), "w") as fp:
        json.dump(snap, fp, indent=2)

    # ---------- deliver to whitelist ----------
    token = E.get("TELEGRAM_BOT_TOKEN")
    if token:
        for cid in load_whitelist():
            try:
                tg_send(None, token, cid, text)
            except Exception as e:
                sys.stderr.write(f"[warn] send to {cid} failed: {e}\n")


if __name__ == "__main__":
    main()
