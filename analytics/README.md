# irx analytics — weekly reports

Standalone studies that read `data/history.jsonl` (written by `../irx_brief.py`)
and deliver a plain-text report to the Telegram whitelist. Stdlib only.

Run everything:

```bash
cd ~/.irx && python3 analytics/weekly.py --dry   # print only
cd ~/.irx && python3 analytics/weekly.py         # deliver to whitelist
```

Individual studies (both accept `--days N` and `--dry`):

| script | question |
|---|---|
| `volatility.py` | Which is more volatile, street USD or USDT? Which has momentum? Does either lead the other? |
| `fair_value.py` | Is street USD priced cheap against other currencies and gold, and is that bias trending? |

Scheduled by cron for **Wednesdays 22:00 Tehran** (18:30 UTC on the VPS) via
`weekly.py`. Add a study by dropping a module in this directory that exposes
`main()` — `weekly.py` discovers it automatically.
