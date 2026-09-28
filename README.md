# IRX — Iran Market Radar

**Tehran street prices next to the world market, in one Telegram message that updates itself.**

IRX samples two markets on two cadences, stores every observation in SQLite, and keeps a single
message per chat that it *edits* — hourly, or the moment you tap **🔄 Refresh**. No message
spam, no cron pile, no LLM anywhere near the numbers.

```
📊 IRX · Mon 28 Sep · 19:07 Tehran

◆ Indices
NASDAQ 100        30,279.2    -0.57%
S&P 500            7,693.0    -0.43%
Nikkei 225        65,533.0    +0.06% ⏸
...
◆ Tehran street
Street USD         244,385    +3.99%
USDT               244,339    +3.69%
Melted gold    105,646,000    +2.28%
Emami coin     246,995,000    +2.70%
...
Tehran 🔵  New York 🔵  Tokyo ⚪  HK ⚪  London ⚪
```

Four buttons: **📈 OHLC** · **🔀 Cross-market** · **📉 Trend 24h** · **🔄 Refresh**, with a way
back to Prices from every page. The OHLC page is delivered as a rendered PNG table, because a
six-column monospace table wraps into noise at phone width.

## What it watches

| Block | Series |
|---|---|
| World | Nasdaq 100, S&P 500, Dow, Nikkei, Hang Seng, DAX, FTSE, ASX 200, WTI, Brent, gold, silver, DXY, EUR/USD, USD/JPY, USD/CNY, USD/AED, BTC |
| Tehran street | USD, USDT, 18k gold, melted gold (آبشده), Emami coin, AED, CNY, JPY (per 100), EUR |

Cadence: **5 min** world, **15 min** Tehran. Reports hourly or on request.

## Cross-market, graded by what it may claim

**Tier A — identity.** Two quotes of the same underlying, or a fixed peg, so a deviation *is* a
real spread. The centrepiece is the **dollar triangulation**: the same dollar priced four
independent ways — street cash, USDT, gold (Tehran 18k ÷ 0.75 against the world ounce) and
Bitcoin (Wallex Toman ÷ world BTC). Measured live on 2026-09-28 the four agreed to **0.55%**
with a 0.07% MAD; the median becomes a consensus, the dispersion a fragmentation gauge, and a
leg breaking away names the dislocated market. Also here: CNY/EUR/AED/JPY Tehran-implied cross
vs world, gold parity, Emami premium over melt value, tether premium.

**Tier B — statistic.** Rolling correlation of returns and robust (median/MAD) z-scores of a gap
against its own 7-day distribution, always printed **with its window and n**, always labelled
non-causal. USDIRT is not structurally linked to WTI or the S&P, and the page never pretends
otherwise.

Deviations are read against their own **half-life** (AR(1)), so a gap that normally reverts in
hours is not confused with one that takes days.

## Market hours

Per-market sessions in each market's own timezone via `zoneinfo`, so DST is the tz database's
problem rather than a hand-maintained offset. Iran's window is **measured from IRX's own sample
history** instead of assumed: Sat–Wed 11:00–19:30 and Thu 11:00–16:30 Tehran, Friday closed. A
frozen feed timestamp overrides any schedule, which is how holidays resolve themselves with no
calendar. Closed rows render with ⏸ and street prices are labelled as the last close; USDT and
BTC stay live.

## Data sources (all keyless, all probed from the host that runs this)

| Source | Role | Note |
|---|---|---|
| [biquote.io](https://biquote.io/docs) | primary world feed (MT5) | each quote carries its own freshness/session state; `/ohlc` backfills bars immediately |
| BrsAPI | Tehran street rates | one bulk call feeds every IR series; needs a browser UA (that host's firewall bans python's) |
| Wallex | Toman-denominated BTC + a second USDT venue | `/v1/markets` costs 46 s cold — only the per-symbol depth path is used |
| Swissquote | spot metals/FX cross-check | keyless, tight spreads |
| CoinGecko, exchangerate-api | crypto and daily-cross cross-checks | keyless |

**Yahoo Finance is deliberately not used**: measured HTTP 429 from two different networks, with
and without the cookie/crumb flow, minutes after it had answered 70/70 symbols. A source that
fails silently would take the whole page with it.

**OPEC basket is not shown**: opec.org is Cloudflare-blocked, its XML is only reachable through
proxies that block us, and it is a daily value — it never belonged on a 5-minute page. The
absence is stated rather than faked.

## Layout

```
irx/         config (series catalogue) · markets (sessions) · store (SQLite)
             net · ingest (both cadences) · cli (one-shot commands)
  sources/   biquote · brsapi · wallex · swissquote/coingecko/erapi
  analysis/  ohlc · trend (deterministic) · cross (Tier A/B) · alerts
  render/    pages (text) · imagetable (PNG tables)
  bot.py     the only always-on process: sampling, pages, alerts, whitelist
tests/       85 tests, stdlib unittest, no network needed (69 without python-telegram-bot)
deploy/      irx-bot.service · rollback-v1.sh
docs/        RESHAPE-PLAN.md — the measured design record
```

One process, one database. systemd runs `venv/bin/python -m irx.bot`; jobs sample both
cadences, evaluate alerts, edit hourly and prune nightly. No cron, no second daemon — the v1
listener is absorbed into the bot, because two long-pollers on one bot token make Telegram
answer 409 to both.

State lives in `data/irx.db` (SQLite, WAL): `sample` for observations, `bar` for OHLC, `chat`
for each chat's single message id and current page, `alert` for the cooldown ledger. Retention:
5-min world samples 90 days, 15-min Tehran 400 days, intraday bars 120 days.

## Running it

```bash
python3 -m venv venv && venv/bin/pip install "python-telegram-bot[job-queue]" pillow
cp .env.example .env && chmod 600 .env     # bot token, owner chat id, source keys
python3 -m irx.cli init                    # create/migrate the database
python3 -m irx.cli ingest all              # one sampling cycle
python3 -m irx.cli backfill                # bars from the feed's own candles
python3 -m irx.cli pages                   # render every page to stdout (sends nothing)
python3 -m irx.cli coverage                # what is stored, per series
venv/bin/python -m irx.bot                 # run the bot
```

Then `systemctl enable --now irx-bot`. To put the **v1 model** back:
`bash deploy/rollback-v1.sh` — it restores the v1 crontab from its backup and re-enables the v1
listener. Nothing was deleted, and `data/irx.db` is left intact for a later retry.

Secrets live only in `.env` (gitignored, mode 600); runtime state lives in `data/`
(gitignored). Nothing in this repository contains a key, a token or a chat id.

## Alerts

A separate message, with a per-rule cooldown (default 60 min): a one-hour move past that
market's own threshold, a Tier-A gap at ≥3 robust sigma from its 7-day norm, tether stress, and
a source that has failed three cycles in a row — so you learn the panel is blind rather than
quiet.

## Not investment advice

Informational only. Markets in restricted economies carry legal and counterparty risks a price
feed cannot see.
