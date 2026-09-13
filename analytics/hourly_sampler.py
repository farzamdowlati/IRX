#!/usr/bin/env python3
"""hourly_sampler.py — append one raw market snapshot as JSONL.

Separate from the brief so sampling frequency can be raised without spamming
Telegram. Designed for cron: silent on success, logs only on failure.

  python3 hourly_sampler.py [--path FILE] [--no-world]

Note on cadence: BrsAPI itself only refreshes most Tehran symbols every
~30-160 minutes (USDT ~46 min, USD/CNY ~130-160 min), so polling faster than
that mostly buys duplicate rows. Each row keeps BrsAPI's own quote time in
`src_time` so true change timing comes from the source, not the poll.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from irx_data import snapshot  # noqa: E402

DEFAULT_PATH = os.path.join(os.path.expanduser("~/.irx/data"), "samples.jsonl")


def main():
    args = sys.argv[1:]
    path = args[args.index("--path") + 1] if "--path" in args else DEFAULT_PATH
    with_world = "--no-world" not in args
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        row = snapshot(with_world=with_world)
    except Exception as e:
        sys.stderr.write(f"[sampler] fetch failed: {e}\n")
        sys.exit(1)
    # dedupe: skip if nothing but the timestamp changed since the last row
    if os.path.exists(path):
        try:
            last = json.loads(open(path).read().strip().split("\n")[-1])
            if all(last.get(k) == row.get(k) for k in
                   ("usd", "usdt", "cny", "aed", "g24", "emami", "btc")):
                return
        except Exception:
            pass
    with open(path, "a") as fp:
        fp.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
