#!/usr/bin/env python3
"""
make_crontab.py — turn .env's INTERVAL_MINUTES / WINDOW_START / WINDOW_END into
crontab lines in the machine's local timezone (Tehran = UTC+3:30 assumed on VPS).
The brief also self-guards its window, so over-wide cron lines are harmless.

Usage:
  python3 make_crontab.py                    # machine = UTC (default)
  python3 make_crontab.py --tz-offset 0      # machine = Tehran
Install:
  ( crontab -l | grep -v irx_brief ; python3 make_crontab.py ) | crontab -
"""
import argparse, sys
from collections import defaultdict

import envcfg as E


def slots(step, start_h, end_h):
    """(hour, minute) pairs every `step` min from start_h:00 to end_h:00 inclusive."""
    t, end = start_h * 60, end_h * 60
    while t <= end:
        yield t // 60, t % 60
        t += step


def compress(hours):
    """[4,5,6,9] -> '4-6,9'"""
    hours = sorted(set(hours))
    out, lo, hi = [], hours[0], hours[0]
    for h in hours[1:]:
        if h == hi + 1:
            hi = h
            continue
        out.append(f"{lo}" if lo == hi else f"{lo}-{hi}")
        lo = hi = h
    out.append(f"{lo}" if lo == hi else f"{lo}-{hi}")
    return ",".join(out)


def crontab_lines(step, start_h, end_h, tz_offset):
    """tz_offset = Tehran minus machine, in hours (UTC machine -> 3.5)."""
    groups = defaultdict(set)
    for h, m in slots(step, start_h, end_h):
        total = (h * 60 + m - int(round(tz_offset * 60))) % (24 * 60)
        groups[total % 60].add(total // 60)
    cmd = f"cd {E.HERE} && /usr/bin/python3 irx_brief.py >> {E.HERE}/cron.log 2>&1"
    return [f"{m} {compress(hrs)} * * * {cmd}" for m, hrs in sorted(groups.items())]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tz-offset", type=float, default=3.5,
                    help="Tehran minus machine timezone (hours). UTC machine: 3.5. Tehran machine: 0.")
    args = ap.parse_args()
    step = E.get_int("INTERVAL_MINUTES", 30)
    ws, we = E.get_int("WINDOW_START", 7), E.get_int("WINDOW_END", 22)
    for line in crontab_lines(step, ws, we, args.tz_offset):
        print(line)
    print(f"# every {step} min, {ws:02d}:00-{we:02d}:00 Tehran; machine-local hours shown above",
          file=sys.stderr)


if __name__ == "__main__":
    main()
