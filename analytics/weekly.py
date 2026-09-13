#!/usr/bin/env python3
"""weekly.py — run every irx analytics report and deliver them to Telegram.

Single entry point for cron: picks up every analytics/*.py module that exposes
main() and feeds its report to the whitelist. Run it with --dry to print.

  python3 analytics/weekly.py [--dry]
"""
import os
import pkgutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import _stats  # noqa: E402

SKIP = {"weekly"}


def main():
    dry = "--dry" in sys.argv
    scripts = sorted(
        m.name for m in pkgutil.iter_modules([HERE])
        if not m.name.startswith("_") and m.name not in SKIP
    )
    print(f"[weekly] {time.strftime('%Y-%m-%d %H:%M:%S')} running: {', '.join(scripts)}")
    ok = 0
    for name in scripts:
        cmd = [sys.executable, os.path.join(HERE, name + ".py")]
        if dry:
            cmd.append("--dry")
        r = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(f"[weekly] {name}: exit={r.returncode}\n")
        if r.stdout:
            sys.stdout.write(r.stdout[-2000:] + "\n")
        if r.stderr:
            sys.stderr.write(f"[weekly] {name} stderr:\n{r.stderr[-2000:]}\n")
        ok += r.returncode == 0
    print(f"[weekly] done: {ok}/{len(scripts)} ok")


if __name__ == "__main__":
    main()
