# IRX — Iran Market Radar

**Tehran street prices vs. the world market — plain-language briefs on Telegram, every 30 minutes.**

BrsAPI (free tier) feeds real street rates in Toman; a live keyless gold feed and
daily world crosses provide the reference side. IRX computes the gaps between what
Tehran implies and what the world says — the arbitrage signal — and broadcasts a
beginner-friendly brief to a whitelisted Telegram audience. An optional
OpenAI-compatible LLM (suggested: Groq's `openai/gpt-oss-20b`) turns today's
intraday history into a two-sentence read. Stdlib Python only — no pip installs.

```
📊 Iran Market Brief — Tue 08 Sep, 19:30 Tehran
Street prices compared with world markets. Toman = what you'd actually pay in Tehran.

💵 US dollar on the street: 226,705 T (today +2.06%)
   USDT (digital dollar): 227,627 T → 0% means both move together (now +0.41%).
   Today's street range so far: 226,660 – 226,715 T · moved -55 T since last brief

🔀 Cross-market check — a drift from ~0 means a pair is priced differently in Tehran than worldwide:
   Yuan: +1.21%  [world 6.728 vs Tehran-implied 6.648] · Δ-0.02pp
   Dirham: +0.58%  [world 3.6725 vs 3.6513] · Δ-0.02pp

🥇 Gold: world ounce $4,386 (live); Tehran gram 31.47M T.
   Parity gram: 31.96M → Tehran sits -1.53% off parity (Δ+0.16pp)
   Emami coin bubble: +0.79% over melt value · Quarter coin: +9.80%

Insight: The USD has slipped slightly, widening the yuan gap while gold remains
cheaper than world parity. Watch the coin premium — it's nearing its intraday high.

Not investment advice — informational only.
```

## What it watches

| Signal | How | Why it matters |
|---|---|---|
| **Yuan / Dirham gap** | world USD-cross vs Tehran-implied cross (`streetUSD ÷ streetCNY`) | both are USD-anchored worldwide, so a persistent drift = one street price lagging → a real spread |
| **Gold parity gap** | `XAU_oz ÷ 31.1035 × streetUSD` vs Tehran 24K gram | the world ounce and the local gram should agree through the dollar; divergence is the local-risk discount/premium |
| **Coin bubbles** | Emami/quarter price vs melt value (8.105 g × 0.900) | manufactured coins trade above gold content; the *rate of change* of that premium is the sentiment gauge |
| **USDT vs cash USD** | Tehran tether vs street dollar | digital-dollar premium = demand pressure under restrictions |

## Why a foreign VPS

Telegram is censored in Iran — the Bot API is unreachable from Iranian IPs — while
BrsAPI is Iran-hosted. IRX is therefore designed to run on a server outside Iran
(verified: Turkish IPs reach both fine) and deliver into Telegram freely.

## Files

| File | Role |
|---|---|
| `envcfg.py` | flat `KEY=VALUE` `.env` reader (stdlib, no python-dotenv) |
| `irx_brief.py` | the scheduled job: fetch → compute → brief → Telegram broadcast |
| `irx_listener.py` | bot service: supervised whitelist (`/add`, `/remove`, `/list`, `/test`) |
| `make_crontab.py` | generates crontab lines from your chosen interval & window |
| `.env.example` | every knob, documented — copy to `.env`, fill, `chmod 600` |

Runtime state lives in `data/` (history `history.jsonl`, whitelist, listener offset) —
gitignored. Secrets have exactly one home: `.env` — gitignored.

## Quick start

```bash
cp .env.example .env && chmod 600 .env   # keys: BrsAPI + exchangerate-api + bot token
python3 irx_brief.py --force             # one-shot test → your Telegram
python3 make_crontab.py | crontab -l ... # see below; then run the listener as a service
```

**Schedule** — pick interval + window in `.env` (`INTERVAL_MINUTES=30`,
`WINDOW_START=7`, `WINDOW_END=22`, Tehran clock) and install:

```bash
( crontab -l | grep -v irx_brief ; python3 make_crontab.py --tz-offset 3.5 ) | crontab -
```

`--tz-offset` = Tehran minus your machine's timezone (UTC machine → `3.5`).
The script also self-guards the window, so loose cron lines are harmless.

**Whitelist** — `irx_listener.py` long-polls your bot. Unknown users can't
subscribe themselves: they get a hold notice and **you** (OWNER_CHAT_ID) get an
access request with their id; `/add <id>` approves, `/remove <id>` revokes,
subscribers `/stop`. Run it under systemd:

```ini
[Service]
ExecStart=/usr/bin/python3 /opt/irx/irx_listener.py
Restart=always
RestartSec=5
```

## Insight LLM (optional)

Any OpenAI-compatible `/chat/completions` endpoint: set `LLM_BASE_URL`,
`LLM_API_KEY`, `LLM_MODEL` — suggested Groq (`https://api.groq.com/openai/v1`,
`openai/gpt-oss-20b`) or OpenRouter (`openai/gpt-oss-20b:free`). Leave them empty
for pure-mechanical briefs. The LLM only *reads* the numbers this repo computed;
it never fetches prices, and errors fail soft (brief sends without the line).

## Operational gotchas (learned in production)

- **User-Agent split:** the Iranian exchange API's firewall IP-bans python's
  default UA → IRX spoofs a browser UA *only for that host*. Cloudflare-fronted
  endpoints (Groq) then error 1010 *at the fake Chrome string* → honest UA for
  LLM/Telegram. Don't "unify" the two.
- **One scheduler:** `history.jsonl` deltas assume consecutive runs from a single
  writer. Two machines appending = broken Δs.
- **Never dry-test the listener against your real `data/whitelist.json`** —
  `/add` writes it for real. Stub `save_wl` in tests.
- Deltas are vs the *previous stored snapshot*; if you change what you compute,
  old rows just miss the field (guarded), no crash.

## Disclaimer

Informational only. Nothing here is investment advice; markets in restricted
economies carry legal and counterparty risks a price feed can't see.
