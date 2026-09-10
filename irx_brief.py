#!/usr/bin/env python3
"""
irx_brief.py — Iran-vs-world market brief (v4). Beginner-friendly, optional
LLM insight. Stdlib only; no pip installs.

Sources (all free tiers):
  * BrsAPI        — Tehran street rates in Toman (needs a browser User-Agent;
                    their firewall bans python's default UA).
  * Swissquote    — live global XAU/USD mid, keyless.
  * exchangerate-api — daily world USD crosses (CNY, AED, EUR) + IRR estimate.

Layout:
  1. Street prices (USD, USDT, AED, CNY, EUR) + USDT/USD premium
  2. Tehran-vs-world cross-rate gaps (AED, CNY, EUR)
  3. Gold: 18k, 24k gram + Emami/Quarter coins, with bubbles against BOTH
     domestic gold price and world parity (official IRT ref rate omitted —
     it has no everyday meaning)
  4. Crypto: BTC, ETH, BNB, XRP, SOL (world USD prices)
  5. Insight (LLM): domestic-vs-international gaps + changes

Change convention on every price line:
  (past-hour: ±amount, ±%) (daily: ±amount, ±%)
computed from data/history.jsonl; falls back to BrsAPI's own daily %.
Appends to history, delivers to Telegram whitelist.
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
LLM_UA = "irx-brief/4.0"
GRAM_PER_OZ = 31.1034768
COIN_GRAM, COIN_PURITY = 8.105, 0.900   # full Bahar Azadi / Emami spec
K18 = 0.75                              # 18k = 75% fine


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
        keys = ("usd", "tether_prem", "gap_aed", "gap_cny", "gap_eur",
                "gold_gap", "gap18", "emami_bub", "emami_bub_dom", "qtr_bub", "btc")
        trend = [{k: r.get(k) for k in ("ts",) + keys} for r in hist_rows[-int(E.get("INSIGHT_TREND_N", "12")):]]
        prompt = (
            "You are writing for Iranians new to markets. Given JSON of NOW and intraday TREND of a "
            "Tehran-vs-world market brief (all percents): gap_aed/gap_cny/gap_eur = how much a dollar "
            "cross is priced differently in Tehran than worldwide (+ means the foreign currency costs "
            "more in Tehran); gold_gap/gap18 = Tehran gram price vs world parity (- means Iranian gold "
            "is cheaper than it should be); emami_bub/qtr_bub = coin premium over its gold content at "
            "world parity, emami_bub_dom = premium over Tehran's own gram price; tether_prem = USDT "
            "over USD on the street; usd/btc = absolute prices.\n"
            f"NOW: {json.dumps(brief)}\nTREND: {json.dumps(trend)}\n"
            "Write EXACTLY 'Insight:' + 3-4 short plain-English sentences covering: (1) what moved most "
            "since earlier today (street dollar, gold, crypto), (2) how far domestic prices sit from "
            "international ones right now and whether that gap widened or narrowed, (3) one thing to "
            "watch next. Quote at most 3-4 numbers total, no advice, no bullets, max 80 words.")
        d = post_json(base.rstrip("/") + "/chat/completions",
                      {"model": model, "max_tokens": E.get_int("LLM_MAX_TOKENS", 500),
                       "temperature": 0.3, "reasoning_effort": "low",
                       "messages": [{"role": "user", "content": prompt}]},
                      headers={"Authorization": f"Bearer {key}"})
        c = (d["choices"][0]["message"].get("content") or "").strip()
        return c[:500] or None
    except Exception as e:
        sys.stderr.write(f"[warn] insight failed: {e}\n")
        return None


def fmt(v):
    return f"{v:+.2f}%" if v is not None else "n/a"


def _tehran_day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts + 3.5 * 3600))


def build_refs(hist, ts):
    """Reference snapshots for the two change parentheses.
    ref_1h: snapshot closest to 1h ago (within 40–80 min, else none).
    ref_day: yesterday's last snapshot; fallback: today's first."""
    today, yday = _tehran_day(ts), _tehran_day(ts - 86400)
    ref_1h = None
    cands = [r for r in hist if 2400 <= ts - r["ts"] <= 4800]
    if cands:
        ref_1h = min(cands, key=lambda r: abs((ts - r["ts"]) - 3600))
    prev_days = [r for r in hist if _tehran_day(r["ts"]) < today]
    if prev_days:
        ref_day = prev_days[-1]
    else:
        todays = [r for r in hist if _tehran_day(r["ts"]) == today]
        ref_day = todays[0] if todays else None
    return ref_1h, ref_day


def money(v):
    if v is None: return "n/a"
    a = abs(v)
    if a >= 1e6: return f"{v/1e6:.2f}M"
    if a >= 1000: return f"{v:,.0f}"
    if a >= 10:   return f"{v:,.2f}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def chg_pair(cur, ref_row, field, brs_pct=None, dec=None):
    """'(past-hour) (daily)' for one price. Amounts inherit money()'s scale."""
    def one(ref, pct_fallback=None):
        if ref is not None and ref.get(field) is not None:
            d = cur - ref[field]
            p = d / ref[field] * 100.0 if ref[field] else None
        elif pct_fallback is not None:
            p = pct_fallback
            d = cur * p / (100.0 + p) if p != -100.0 else None
        else:
            return "(—)"
        ds = money(d) if d is not None else "—"
        return f"({ds}, {p:+.2f}%)" if dec is None else f"({d:+,.{dec}f}, {p:+.2f}%)"
    h1, hd = one(ref_row[0]), one(ref_row[1], brs_pct)
    return f"{h1} {hd}"


def main():
    force = "--force" in sys.argv
    dry = "--dry" in sys.argv or "--no-send" in sys.argv
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
        era = {c: e["conversion_rates"][c] for c in ("CNY", "AED", "EUR")}
    except Exception as ex:
        sys.stderr.write(f"[warn] ExchangeRate-API failed: {ex}\n")

    # ---------- compute ----------
    pct = lambda a, b: (a / b - 1.0) * 100.0
    px = lambda s: brs[s]["price"]
    ch = lambda s: brs[s]["chg"]

    usd, usdt, aed, cny, eur = px("USD"), px("USDT_IRT"), px("AED"), px("CNY"), px("EUR")
    g18, g24, emami, quarter = px("IR_GOLD_18K"), px("IR_GOLD_24K"), px("IR_COIN_EMAMI"), px("IR_COIN_QUARTER")
    btc, eth, bnb, xrp, sol = px("BTC"), px("ETH"), px("BNB"), px("XRP"), px("SOL")

    imp = {"AED": usd / aed, "CNY": usd / cny, "EUR": usd / eur}
    gaps = {k: (pct(era[k], v) if era else None) for k, v in imp.items()}

    theo_g = xau * usd / GRAM_PER_OZ          # world-parity 24k gram, Toman
    theo_18 = theo_g * K18
    gold_gap, gap18 = pct(g24, theo_g), pct(g18, theo_18)
    melt_w_em, melt_w_q = theo_g * COIN_GRAM * COIN_PURITY, theo_g * (COIN_GRAM / 4) * COIN_PURITY
    melt_d_em, melt_d_q = g24 * COIN_GRAM * COIN_PURITY, g24 * (COIN_GRAM / 4) * COIN_PURITY
    emami_bub, emami_bub_dom = pct(emami, melt_w_em), pct(emami, melt_d_em)
    qtr_bub, qtr_bub_dom = pct(quarter, melt_w_q), pct(quarter, melt_d_q)
    tether_prem = pct(usdt, usd)

    hist = load_history()
    prev = hist[-1] if hist else None
    ref_1h, ref_day = build_refs(hist, ts)
    day_rows = [r for r in hist if _tehran_day(r["ts"]) == _tehran_day(ts)]
    usd_lo = min((r["usd"] for r in day_rows), default=usd)
    usd_hi = max((r["usd"] for r in day_rows), default=usd)

    brief = {"usd": round(usd), "tether_prem": round(tether_prem, 3),
             "gap_aed": None if gaps["AED"] is None else round(gaps["AED"], 3),
             "gap_cny": None if gaps["CNY"] is None else round(gaps["CNY"], 3),
             "gap_eur": None if gaps["EUR"] is None else round(gaps["EUR"], 3),
             "gold_gap": round(gold_gap, 3), "gap18": round(gap18, 3),
             "emami_bub": round(emami_bub, 3), "emami_bub_dom": round(emami_bub_dom, 3),
             "qtr_bub": round(qtr_bub, 3), "qtr_bub_dom": round(qtr_bub_dom, 3),
             "xau_global": xau, "btc": btc, "eth": eth, "bnb": bnb, "xrp": xrp, "sol": sol}

    # ---------- compose ----------
    interval_min = E.get_int("INTERVAL_MINUTES", 30)
    refs = (ref_1h, ref_day)
    L = [f"📊 Iran Market Brief — {now:%a %d %b, %H:%M} Tehran",
         "Street prices in Toman vs world markets. Changes:",
         "(past hour) (today vs yesterday close).",
         f"Updates every {interval_min} min, 07:00–22:00.",
         "",
         "💵 Currencies on the street (Toman)",
         f"   USD  {money(usd)} T " + chg_pair(usd, refs, "usd", ch("USD")),
         f"   USDT {money(usdt)} T " + chg_pair(usdt, refs, "usdt", ch("USDT_IRT")),
         f"   AED  {money(aed)} T " + chg_pair(aed, refs, "aed", ch("AED")),
         f"   CNY  {money(cny)} T " + chg_pair(cny, refs, "cny", ch("CNY")),
         f"   EUR  {money(eur)} T " + chg_pair(eur, refs, "eur", ch("EUR")),
         f"   USDT/USD: {fmt(tether_prem)} — 0% means digital and paper dollar move together"
         + (f" (Δ{tether_prem - prev['tether_prem']:+.2f}pp vs last brief)"
            if prev and prev.get("tether_prem") is not None else ""),
         ""]
    if era:
        L.append("🔀 Tehran vs world — what a cross costs in Tehran vs worldwide:")
        for name, k, dec in (("Dirham", "AED", 4), ("Yuan", "CNY", 3), ("Euro", "EUR", 4)):
            g = gaps[k]
            row = (f"   {name}: {fmt(g)}  [world {era[k]:.{dec}f} vs Tehran-implied {imp[k]:.{dec}f}]")
            pk = "gap_" + k.lower()
            if prev and prev.get(pk) is not None and g is not None:
                row += f" · Δ{g - prev[pk]:+.2f}pp"
            L.append(row)
        L.append("   + = that currency costs more in Tehran than worldwide; ±1% is normal noise.")
        L.append("")
    L += ["🥇 Gold (world ounce " + f"${xau:,.2f} — {'live' if xau_live else 'BrsAPI, may be stale'}" + "; Tehran parity gram " + f"{money(theo_g)} T)",
          f"   24k gram {money(g24)} T " + chg_pair(g24, refs, "g24", ch("IR_GOLD_24K"))
          + f" → vs world parity {fmt(gold_gap)}",
          f"   18k gram {money(g18)} T " + chg_pair(g18, refs, "g18", ch("IR_GOLD_18K"))
          + f" → vs world parity {fmt(gap18)}",
          f"   Emami    {money(emami)} T " + chg_pair(emami, refs, "emami", ch("IR_COIN_EMAMI")),
          f"      bubble vs gold (Tehran) {fmt(emami_bub_dom)} · vs gold (world parity) {fmt(emami_bub)}",
          f"   Quarter  {money(quarter)} T " + chg_pair(quarter, refs, "quarter", ch("IR_COIN_QUARTER")),
          f"      bubble vs gold (Tehran) {fmt(qtr_bub_dom)} · vs gold (world parity) {fmt(qtr_bub)}",
          "   Small coins always carry a minting premium — watch how fast it changes.",
          "",
          "₿ Crypto (world price, USD)",
          f"   BTC  {money(btc)} $ " + chg_pair(btc, refs, "btc", ch("BTC")),
          f"   ETH  {money(eth)} $ " + chg_pair(eth, refs, "eth", ch("ETH")),
          f"   BNB  {money(bnb)} $ " + chg_pair(bnb, refs, "bnb", ch("BNB")),
          f"   XRP  {money(xrp)} $ " + chg_pair(xrp, refs, "xrp", ch("XRP")),
          f"   SOL  {money(sol)} $ " + chg_pair(sol, refs, "sol", ch("SOL")),
          ""]
    ins = llm_insight(brief, hist)
    if ins:
        L += [ins if ins.startswith("Insight") else f"Insight: {ins}", ""]
    alert = [f"{n} {fmt(v)} past {thr}%" for k, n, thr in
             (("gap_aed", "dirham gap", E.get_float("ALERT_GAP_PCT", 2.0)),
              ("gap_cny", "yuan gap", E.get_float("ALERT_GAP_PCT", 2.0)),
              ("gap_eur", "euro gap", E.get_float("ALERT_GAP_PCT", 2.0)),
              ("gold_gap", "gold vs world", E.get_float("ALERT_GOLD_PCT", 3.0)),
              ("emami_bub", "Emami bubble", E.get_float("ALERT_COIN_PCT", 8.0)),
              ("qtr_bub", "Quarter bubble", E.get_float("ALERT_COIN_PCT", 12.0)))
             if (v := brief[k]) is not None and abs(v) >= thr]
    if alert:
        L += ["⚠ " + "; ".join(alert), ""]
    L += ["Not investment advice — informational only."]

    text = "\n".join(L)
    print(text)

    # ---------- persist ----------
    snap = dict(brief, ts=ts, usdt=usdt, aed=aed, cny=cny, eur=eur,
                g24=g24, g18=g18, emami=emami, quarter=quarter,
                imp_aed=round(imp["AED"], 4), imp_cny=round(imp["CNY"], 4),
                imp_eur=round(imp["EUR"], 4), theo_g=round(theo_g), insight=ins)
    with open(os.path.join(E.DATA_DIR, "history.jsonl"), "a") as fp:
        fp.write(json.dumps(snap) + "\n")
    with open(os.path.join(E.DATA_DIR, "latest.json"), "w") as fp:
        json.dump(snap, fp, indent=2)

    # ---------- deliver to whitelist ----------
    token = E.get("TELEGRAM_BOT_TOKEN")
    if token and not dry:
        for cid in load_whitelist():
            try:
                tg_send(None, token, cid, text)
            except Exception as e:
                sys.stderr.write(f"[warn] send to {cid} failed: {e}\n")


if __name__ == "__main__":
    main()
