---
title: IRX v2 — reshape plan
date: 2026-09-28
tags: [irx, telegram, market-data, plan]
---

# IRX v2 — reshape plan (skeleton + decisions)

Status: **design, pending build**. Author: Hermes. Date: 2026-09-28.
Evidence tags: **V** = verified by running a command (output quoted), **S** = sourced from a doc/page, **U** = assumption to be checked during build.

## 1. What IRX is today, and what changes

Today IRX is a *broadcast* stack: cron fires `irx_brief.py` every 30 min, it composes a beginner-friendly text brief and pushes a **new** Telegram message; three more cron jobs sample history, write a nightly OHLC/trend report and a nightly LLM insight. State lives in three ad-hoc JSONL files (`history.jsonl`, `samples.jsonl`, `daily.jsonl`) that have already drifted schema (V: `history.jsonl` rows have no `src_time` key, `samples.jsonl` rows do).

IRX v2 inverts that: **one always-on process owns everything** — it samples both markets, stores every observation in a real database, and *edits one Telegram message per chat* that the user navigates with buttons. Nothing is pushed unless it is an alert.

| User's point | Change |
|---|---|
| 1 · series list | New catalogue (§4). Adds international side (FX/equities/oil/metals/DXY/BTC) that did not exist; trims Iranian side to the requested set (drops quarter coin, and no longer shows them in a brief). |
| 2 · cadence + storage | International **5 min**, Iranian **15 min**, into **SQLite** (§5). Reports **hourly** or on demand — not every 30 min. |
| 3 · bot stack | Rewritten on **python-telegram-bot 22.8** (V: already installed at `/root/.ghtrend/venv`, Python 3.10.12) as one asyncio process; the old `irx_listener.py` + its systemd unit are absorbed. |
| 4 · format | One **edited** message per chat with inline pages: Prices → OHLC / Cross-market / Trend-24h / Back (§7). |
| 4b · no nightly reading | `analytics/daily_report.py`, `analytics/daily_insight.py` and the LLM path are **removed**; the time data stays in the DB and feeds OHLC + trend on demand. |
| 5 · alerts | Sharp-move + cross-market anomaly rules push a **separate** message (§8). |
| 6 · market hours | Session engine with per-market hours + DST, plus Iran's window measured from data (§6). |
| 7 · cross-market | Split into **Tier A** (parity identities — provable) and **Tier B** (measured statistics, explicitly not causal) (§9). |

## 2. Recon evidence (measured live, 2026-09-28)

**Yahoo Finance is not usable in production.** V: `v8/finance/chart/^GSPC` → HTTP 429 `Too Many Requests` from **tr** *and* from this Mac, with and without the cookie/crumb flow (`/v1/test/getcrumb` → 429 both hosts). It answered 70/70 symbols at 10:30 UTC today and was throttled by 12:00 UTC. This matches the existing skill note ("Yahoo rate-limits hard — skip it").
⇒ Yahoo is demoted to an *opportunistic* source that must never be required for a page to render.

**biquote.io — keyless, no signup, MT5 feed, and the right shape.** V:
- `GET /api/{symbol}` → `mid`, `bid`, `ask`, `quoteAgeSeconds`, `stale`, `marketState`, `dayDiffPercent`, `high`, `low`, `timestamp`, `source`. Richer than a plain price: it carries its **own freshness and session state**, which the renderer can trust over our guesses.
- `GET /api/{symbol}/ohlc?interval=1m|5m|15m|30m|1h|4h|1d&count=N` → OHLC bars ⇒ **history can be backfilled immediately** instead of waiting days for samples to accrue. V: 5m → 101 bars (oldest 05:45Z), 15m → 101 bars (oldest 2026-09-25), 1h → 101 bars (oldest 2026-09-22).
- `GET /api/symbols` → 1,850 symbols; `/api/symbols/search?q=`; `/api/active` → 458 live symbols.
- **Latency is the constraint**: V 22 symbols in 70.8 s serial = **3.2 s per request**. The sampler must be concurrent (or batched) or it will not fit a 5-minute cadence.
- **Coverage gaps found** (symbol exists in the directory but `mid` is null): `NAS100`, `CHINA50`, `CHINA_H`, `SPX500`, `GER40`, `FRA40`, `ESTX50`, `NGAS`, `JPN225`, `USDINDEX`.
  Working substitutes: `USTEC` for Nasdaq, `HK50`, `JP225`, `DE30`, `UK100`, `AUS200`, `DXY`.
- **No Shanghai index, no KOSPI/Taiwan/Sensex.** ⇒ Asia ex-Japan/HK is an open item (§10).

**Other sources, re-verified.** V: Swissquote spot (`XAU/USD` 4144.58/4145.24, `XAG/USD`, `EUR/USD`, `USD/JPY` — tight real-time, but **empty** for `USD/CNY`, `USD/AED`, `USD/IRR`);
CoinGecko keyless (`bitcoin` 83405); `open.er-api.com/v6/latest/USD` keyless daily crosses;
Treasury keyless XML (200, 286 KB). **OPEC basket: no working keyless route** — opec.org 403 (Cloudflare), Jina 403 now (it succeeded once), Wayback 429, allorigins/codetabs 522, corsproxy refuses keyless, TradingEconomics is JS-rendered (no numbers in HTML).

**Iranian market window, measured from IRX's own 286 stored samples.** V, from
BrsAPI's `src_time` lag bucketed by Tehran weekday/hour:

| Tehran | observed | verdict |
|---|---|---|
| Sat–Wed 11:00–19:00 | quote lag 30–70 s | **open** |
| Sat–Wed 20:00–21:00 | lag 1,800 s then 5,400 s, frozen | closed (froze ~19:30) |
| Thu 11:00–16:00 | lag 37–73 s | **open** — the assumption "closed Thursday 15:00" is wrong |
| Thu 17:00+ | frozen at ~16:30 | closed |
| Fri all day | lag grows 48,604 → 102,605 s | closed (source frozen since Thu 16:25) |

⇒ Iran's session is **Sat 11:00–19:30, Thu 11:00–16:30 Tehran**, and the engine additionally trusts *staleness* over any schedule — which is also how holidays resolve themselves.

## 3. Architecture

One process, one datastore, no cron maze.

```
systemd: irx-bot.service  ──> irx/bot.py  (python-telegram-bot 22.8, asyncio)
                                ├── JobQueue run_repeating: intl sampler   (5 min)
                                ├── JobQueue run_repeating: iran sampler   (15 min)
                                ├── JobQueue run_repeating: hourly report  (edit-in-place)
                                ├── JobQueue run_daily:     OPEC fetch     (daily, if enabled)
                                ├── callback handlers:      page buttons
                                └── message handlers:       /start /refresh /stop, owner /add /remove /list
                                        │
                            irx/ingest.py ───> irx/store.py ──> data/irx.db (SQLite, WAL)
                                        │                ▲
                            irx/sources/*.py             │
                                        │        irx/analysis/{ohlc,trend,cross,alerts}.py
                                        └────────> irx/render/pages.py
```

```
~/.irx/
  docs/RESHAPE-PLAN.md        this file
  envcfg.py                   flat .env reader (keep)
  irx/
    config.py                 env + SERIES catalogue + source priority chains
    markets.py                session engine (per-market hours + DST + staleness)
    store.py                  SQLite schema/queries: samples, bars, chats, msg state, alerts
    ingest.py                 cadence runners, concurrency, failure policy
    sources/{biquote,brsapi,swissquote,coingecko,erapi,opec}.py
    analysis/{ohlc,trend,cross,alerts}.py
    render/pages.py           page text + inline keyboards
    bot.py                    PTB application (the only always-on process)
    cli.py                    one-shot commands for testing/deploy
  tests/                      stdlib unittest with synthetic fixtures
  deploy/irx-bot.service
  data/irx.db                 (gitignored)
```

## 4. Series catalogue

`id` = our stable key; `src` = provider symbol; `page` = where it renders.

**International — 5 min.** Priority chain is tried in order; a failed first source falls back, and a series with no fresh value renders as `—` with a stale badge rather than dying.

| id | requested | primary (biquote) | cross-check | session |
|---|---|---|---|---|
| `EURUSD` | EURUSD | `EURUSD` | Swissquote `EUR/USD` | FX 24×5 |
| `USDJPY` | JPYUSD | `USDJPY` | Swissquote `USD/JPY` | FX 24×5 |
| `USDCNY` | CNYUSD | `USDCNY` (spot, age ≤240 s) | `USDCNH` offshore · er-api daily | FX 24×5 |
| `USDAED` | AEDUSD | `USDAED` | — (hard peg 3.6725) | FX 24×5 |
| `NASDAQ` | NASDAQ | `USTEC` | — | US cash 13:30–20:00 UTC |
| `SP500` | SP500 | `US500` | — | US cash 13:30–20:00 UTC |
| `DJI` | (extra) | `US30` | — | US cash |
| `NIKKEI` | NIKKEI | `JP225` | — | Tokyo 00:00–06:00 UTC |
| `HK` | HK | `HK50` | — | HK 01:30–08:00 UTC |
| `SHANGHAI` | SHANGHAI | **open item §10** | — | Shanghai 01:30–07:00 UTC |
| `KOSPI`,`TAIEX`,`SENSEX` | "other major Asian" | **open item §10** | — | — |
| `DAX`,`FTSE`,`ASX` | (extra) | `DE30`,`UK100`,`AUS200` | — | EU/AU cash |
| `WTI` | WTI | `USOIL` | er-api? no · Swissquote? no | NYMEX 22:00–21:00 UTC 24×5 |
| `BRENT` | Brent | `UKOIL` | — | 24×5 |
| ~~`OPEC`~~ | OPEC basket | **dropped** (§10.2) — no keyless route exists | — | — |
| `XAUUSD` | XAUUSD | `XAUUSD` (COMEX) | **Swissquote spot** (tightest) | 24×5 |
| `XAGUSD` | XAGUSD | `XAGUSD` | Swissquote spot | 24×5 |
| `DXY` | DXI | `DXY` | — | 24×5 |
| `BTCUSD` | $BTC | `BTCUSD` (24/7) | CoinGecko · BrsAPI `BTC` | 24/7 |

**Iranian — 15 min** (BrsAPI `Gold_Currency.php`). User's list, with the two traps found:

| id | BrsAPI symbol | note |
|---|---|---|
| `USDIRT` | `USD` | street cash dollar |
| `USDTIRT` | `USDT_IRT` | 24/7 — the only live USD anchor while Iran is closed |
| `G18` | `IR_GOLD_18K` | per gram |
| `G_ABSHODE` | `IR_GOLD_MELTED` | "آبشده نقدی". **Unit trap**: 105,385,000 vs 18k gram 24,417,300 ⇒ it is a *mesghal-scale* quote, not a gram. Exact unit/purity to be derived in §10 before it appears on any page (**U**). |
| `EMAMI` | `IR_COIN_EMAMI` | Emami coin |
| `AEDIRT` | `AED` | per dirham |
| `CNYIRT` | `CNY` | per yuan |
| `JPYIRT` | `JPY` | **per 100 yen** (V: 153,943) — divide by 100 before any cross math |
| `EURIRT` | `EUR` | per euro |
| ~~quarter~~ | ~~`IR_COIN_QUARTER`~~ | **removed** per spec |
| `G24` | `IR_GOLD_24K` | kept in DB only: needed to price coin melt value; not on the price page (not in the requested list) |

Also dropped from display: `IR_COIN_1G/HALF/BAHAR`, GBP/KWD/AUD/CAD/…/GEL, and the 12 non-BTC cryptos. BrsAPI returns them in one call, so **the raw payload is archived** and those series can be re-enabled without new plumbing.

## 5. Storage — SQLite, and why

The current three JSONL files are replaced by `data/irx.db` (SQLite, WAL, stdlib `sqlite3`; V: tr ships 3.37.2). Rejected: Influx/Timescale/QuestDB — extra daemon, RAM and failure mode on a small
VPS for **~7,200 rows/day** (22 intl × 288 + 9 iran × 96). A time-series DB buys nothing at this size; SQLite gives indexed range scans, one-writer semantics, and atomic updates of bot state.

```sql
series   (id TEXT PK, kind TEXT, source TEXT, tz TEXT, session TEXT, unit TEXT, label TEXT)
sample   (series_id TEXT, ts INTEGER, value REAL, src_ts INTEGER, source TEXT, stale INTEGER,
          PRIMARY KEY (series_id, ts))                     -- one row per observation
bar      (series_id TEXT, interval TEXT, open_ts INTEGER, o REAL, h REAL, l REAL, c REAL,
          n INTEGER, src TEXT, PRIMARY KEY (series_id, interval, open_ts))
chat     (chat_id INTEGER PK, kind TEXT, added_ts INTEGER, msg_id INTEGER, page TEXT,
          anchor TEXT, state_json TEXT)                    -- one navigable message per chat
alert    (id INTEGER PK, ts INTEGER, kind TEXT, series_id TEXT, detail TEXT, sent INTEGER)
meta     (k TEXT PK, v TEXT)                               -- crumb/cookie cache, last OPEC, schema ver
```

Retention: raw `sample` 5-min international 90 days, Iranian 15-min 400 days, `bar` 1d/1h forever.
Backfill on first run: `bar` from biquote `/ohlc` (3 weeks of 15m, 1 week of 5m, 6 days of 1h).

## 6. Market-hours engine (`markets.py`)

- Each series carries a `session`: `crypto` (24/7), `fx` (Sun 21:00 → Fri 21:00 UTC), `futures`
  (Sun 22:00 → Fri 21:00 UTC, daily break 21:00–22:00), `cash:{exchange}` (Tokyo, HK, Shanghai,
  Seoul, Taipei, Mumbai, US, Frankfurt, London, Sydney — weekday rules **in the exchange's own timezone via `zoneinfo`**, so DST is correct without hand-maintained UTC offsets), `iran`.
- Iran: **Sat 11:00–19:30, Thu 11:00–16:30 Tehran** (measured, §2) **plus** a staleness override —
  if BrsAPI's `src_time` has not advanced for >10 min, the market is closed regardless of schedule.
  This is exactly the user's rule ("an unchanged USDIRT on Thursday means closed"), generalised so holidays need no calendar.
- Every rendered row shows its state: `•` live, `⏸` closed (last close + how old), `—` no data.
- While Iran is closed the panel says so and marks street USD as the **last close**, and USDT becomes the only live USD anchor (user's point 6) — the Tehran-implied cross rows state that they are computed on a stale street dollar.

## 7. Telegram surface

One message per chat, created on first report and **edited** thereafter (`edit_message_text`, `edit_message_reply_markup`). Pages are pure functions of the DB, so a page can be re-rendered without re-fetching anything.

```
📊 IRX · 14:00 Tehran (10:30 UTC)          [Prices]   ← default page
   … prices …
[📈 OHLC] [🔀 Cross-market] [📉 Trend 24h] [🔄 Refresh]
```

- **Prices** — the requested list, grouped: World (indices/FX/oil/metals/DXY/BTC) · Tehran street · each row `value (±Δ vs 1h) (±Δ vs prev close) state-badge`, closed markets dimmed.
- **OHLC** — per-series O/H/L/C for today + yesterday's close, from `bar` (5m aggregated to the requested interval); a compact table, no charts.
- **Cross-market** — §9, **no LLM text**.
- **Trend 24h** — deterministic: OLS slope ÷ standard error of that asset's own measured per-step noise, gated by an efficiency ratio ⇒ `Bullish / Bearish / Sideways`; same history always gives the same verdict. Reuses the existing `analytics/trend.py` engine.
- **Back** → Prices. **Refresh** → force an ingest of the visible page's series, then re-render.
- Hourly edit is silent (no notification churn) — Telegram edits do not notify.
- **Decided 2026-09-28: the hourly edit is skipped** when every series on the visible page is closed *and* no displayed value changed since the last render (a content hash decides). The panel is still regenerated on demand via **Refresh**, so a closed-market night costs nothing and the user never sees a no-op edit.
- Whitelist/owner flow is preserved: unknown user → hold notice + `/add <id>` to the owner.

## 8. Alerts (`analysis/alerts.py`)

Separate message, own cooldown per rule (default 60 min) to avoid spam. **Decided 2026-09-28: every whitelisted chat receives them**, as separate messages (not folded into the hourly edit).

| rule | default trigger |
|---|---|
| sharp move | 1h move ≥ `ALERT_MOVE_PCT` (2.0% FX/metals/oil, 3.0% indices, 5.0% BTC) |
| Iran street jump | USDIRT/EMAMI 1h ≥ 1.5% |
| cross anomaly | a Tier-A gap's z-score vs its own 7-day distribution ≥ 3, or the gap widening ≥ 0.5 pp in 1h |
| tether stress | USDT premium ≥ +2% or ≤ −1% vs street USD |
| data outage | a required source fails ≥ 3 consecutive cycles (so the user knows the panel is blind) |

## 9. Cross-market analysis, split by what is provable

**Tier A — parity identities** (ratios that must hold if both sides are priced in the same dollar;
a persistent deviation *is* a real spread — the user's "deterministic and mathematically proven"):

1. `AEDIRT` vs the 3.6725 peg and vs world `USDAED` → local-vs-peg premium.
2. `CNYIRT`, `JPYIRT` (÷100!), `EURIRT` → Tehran-implied cross (`USDIRT ÷ CNYIRT`) vs world cross.
3. Gold parity: world oz (`XAUUSD`) → `oz/31.1035 × USDIRT` = theoretical gram vs Tehran `G18/0.75`
   and `G_ABSHODE` (once §10 fixes its unit).
4. Emami coin premium over **melt value**, on both bases (Tehran gold and world-parity gold) — the *rate of change* of this premium is the sentiment series.
5. Tether premium `USDTIRT/USDIRT − 1` (demand pressure under restriction).
6. BTC-implied dollar: `BrsAPI BTC (Toman) ÷ world BTC-USD` → a **second, independent USDIRT estimate** to triangulate with USDT. Valid 24/7 — the closed-hours workaround.

**Tier B — measured statistics, explicitly not causal** (the user's point 7): USDIRT is *not*
structurally linked to WTI or the S&P, and we say so. For each pair (`USDIRT`↔oil, ↔US indices,
↔DXY, ↔BTC, and any missing cross) report the **rolling 24h/7d Pearson correlation of returns**
plus the **z-score of the current ratio** against its own 7-day distribution — labelled `statistical, not causal`, and `n`/window shown so a short sample can't masquerade as a signal.
No pair is reported as "correlated" without the coefficient, the window and the sample count.

## 10. Open items (must close before the affected page ships)

1. **Shanghai + other Asian cash indices** — biquote has none. Candidate keyless routes to probe:
   Sina `hq.sinajs.cn/list=s_sh000001` (needs `Referer: finance.sina.com.cn`), Tencent `qt.gtimg.cn/q=sh000001`, Eastmoney `push2.eastmoney.com/api/qt/stock/get`, Naver for KOSPI,
   TWSE for TAIEX. Yahoo stays as opportunistic-only.
2. **OPEC basket** — every keyless route is blocked (§2). Options: (a) a headless-Chrome fetch on tr once a day, (b) drop it from the panel and document why, (c) a paid/keyed source. Needs a decision — it is the only requested series with no route today.
3. **`IR_GOLD_MELTED` unit** — ratio-to-18k is stable-looking but unverified; derive the unit and purity before showing any coin premium derived from it.
4. **biquote concurrency** — 3.2 s/request serial; verify that 4–6 concurrent requests are tolerated (docs claim 15,000 req/min) and measure the real 22-symbol cycle time.
5. **Single-source risk** — most of the world page will come from one small independent service.
   Cross-checks are wired (Swissquote, CoinGecko, er-api) but coverage is thin for indices.

## 11. Cutover

1. Build + test locally (`--dry`, fixtures), never against production state.
2. Ship to tr in a fresh venv (`python3 -m venv`; note stock `python3` has no `ensurepip`) as `irx-bot.service`; run it **alongside** the existing cron briefly, comparing its pages against the old brief's numbers for the same timestamps.
3. Then disable the old cron lines (`irx_brief.py` 30-min, `hourly_sampler.py`, `daily_report.py`, `daily_insight.py`, weekly) and `systemctl disable irx-listener` — the bot absorbs the listener.
4. Nothing is deleted from tr until step 2 has produced matching numbers; the old scripts stay on disk as `.bak` for rollback.
