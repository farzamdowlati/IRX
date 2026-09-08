# irx — Iran market brief & Tehran-vs-world arbitrage gaps

A tiny, stdlib-only Python tool that watches Iranian street prices against the
world market and posts a plain-language brief to Telegram.

**What it does (every interval, e.g. every 30 min, 07:00–22:00 Tehran):**

| Line | Meaning |
|---|---|
| 💵 USD / USDT street | Tehran rates from **BrsAPI** (free tier), plus USDT-vs-cash premium |
| 🔀 Yuan / Dirham gap | World cross-rate (exchangerate-api) vs Tehran-implied cross — a drift past ±1–2% means one side is priced differently in Tehran → the arbitrage signal |
| 🥇 Gold parity | World ounce (**Swissquote live feed**, keyless) × street USD ÷ 31.1035 g vs Tehran gram price |
| 🪙 Coin bubbles | Emami / quarter coin vs their melt (gold-content) value |
| Insight | 2–3 sentences of pattern-reading from any OpenAI-compatible LLM (suggested: Groq `openai/gpt-oss-20b`) over today's intraday history — optional; the brief works fine without it |

All claims in a brief come from the two fetches above; the LLM only *reads*
those numbers and never invents prices. Not investment advice.

## Why it lives on a foreign VPS

Telegram is censored in Iran: the bot API is unreachable from Iranian IPs and
gets blocked quickly if polled directly. Run `irx_brief.py` + `irx_listener.py`
on a server outside Iran (e.g. Turkey) where both BrsAPI and Telegram respond.
(Verified: BrsAPI does not geo-block Turkish IPs.)

## Files

| File | Role |
|---|---|
| `envcfg.py` | flat `KEY=VALUE` reader for `.env` (stdlib, no python-dotenv) |
| `irx_brief.py` | the scheduled job: fetch → compute gaps → brief → Telegram broadcast |
| `irx_listener.py` | long-poll bot service: whitelist management (see below) |
| `make_crontab.py` | generates crontab lines from `.env` schedule settings |
| `config.json` | **legacy** (v2); replaced by `.env` — not in the repo |
| `data/` | runtime state, gitignored: `history.jsonl`, `latest.json`, `whitelist.json`, `listener_offset` |

## Setup

```bash
git init / (already done locally)          # this repo is intentionally NOT pushed to GitHub
cp .env.example .env && chmod 600 .env     # fill in keys
python3 irx_brief.py --force               # one-shot test (sends to whitelist!)
```

Keys to get (all free): BrsAPI key (email registration), exchangerate-api key,
bot token from @BotFather. LLM is optional — see `.env.example` suggestions
(Groq or OpenRouter); leave the `LLM_*` values empty for pure-mechanical briefs.

**Whitelist / second people:** start `irx_listener.py` as a service (below).
Nobody can subscribe themselves: an unknown user gets a hold notice and *you*
receive an access request with their numeric id — `/add <id>` approves,
`/remove <id>` revokes, `/list` shows, `/test` pings everyone. Subscribers can
`/stop` anytime. Owner is `OWNER_CHAT_ID` in `.env`.

**Schedule:** edit `INTERVAL_MINUTES` / `WINDOW_START` / `WINDOW_END` in `.env`,
then regenerate cron on the machine:

```bash
( crontab -l | grep -v irx_brief ; python3 make_crontab.py ) | crontab -
```

`make_crontab.py --tz-offset 3.5` (default) assumes the machine clock is UTC.
The brief double-guards the window itself, so cron firing at "off" times is
harmless. If Telegram should *not* go out (e.g. a dry-run box), empty the
whitelist file `data/whitelist.json` to `[]` — sends target that list only.

**systemd unit for the listener (VPS):**

```ini
[Unit]
Description=irx brief bot whitelist listener
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /root/.irx/irx_listener.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

`systemctl enable --now irx-listener`

## Operational gotchas (learned the hard way)

- **User-Agent split:** BrsAPI's firewall IP-bans python's default UA → the tool
  spoofs a Chrome UA *only for BrsAPI*. Groq/Cloudflare then error 1010 *at the
  browser UA* → honest `irx-brief/3.0` UA for LLM + Telegram. Both handled in code.
- **History deltas need consecutive runs:** `history.jsonl` is one JSON line per
  brief; the "Δpp" figures compare against the previous line, so don't let two
  schedulers (local + VPS) write to the same file.
- **Never dry-test `irx_listener.py` against a real whitelist file** — its
  `/add`/`/remove` handlers write `data/whitelist.json` for real. Stub
  `save_wl()` in tests (see git history of this README's first drafts).
- `config.json` (v1/v2 era) held keys flat; kept on the VPS until cutover, then
  deleted. Secrets have exactly one home: `.env`.

## Status

- v3 (`.env`-based) written 2026-09-08; VPS cutover pending this deploy.
- Production until cutover: v2 + `config.json` on the VPS (`root@tr`).
- No remote configured: local repo only, by design.
