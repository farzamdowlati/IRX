#!/usr/bin/env python3
"""irx/cli.py — one-shot commands for testing, cron-free operation and deploys.

Run as a module from the repo root:   python3 -m irx.cli <cmd>

  init                 create/migrate the DB and register the series catalogue
  ingest intl|iran|all run one sampling cycle (--dry prints instead of writing)
  backfill [--intervals 5m,15m,1h]
  coverage             what is in the DB: rows, span, staleness per series
  health               probe every provider and report what answers
  prune                apply retention
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import config as C, ingest, markets as M, store as S


def _fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) + "Z"


def cmd_init(args):
    conn = S.connect()
    print("db: %s (schema %s)" % (C.DB_PATH, S.get_meta(conn, "schema_version")))
    print("series registered: %d" % len(C.SERIES))
    return 0


def cmd_ingest(args):
    conn = S.connect()
    which = args.what
    out = {}
    if which in ("intl", "all"):
        out["intl"] = ingest.run_intl(conn, crosscheck=not args.no_crosscheck)
    if which in ("iran", "all"):
        out["iran"] = ingest.run_iran(conn)
    print(json.dumps(out, indent=1, default=str))
    return 0 if all(not v.get("fail") for v in out.values()) else 1


def cmd_backfill(args):
    conn = S.connect()
    ivs = tuple(x.strip() for x in args.intervals.split(",") if x.strip())
    out = ingest.backfill_bars(conn, intervals=ivs, count=args.count)
    print("backfill: %d bars across %d series, %d error(s)"
          % (out["bars"], out["series"], len(out["errors"])))
    for e in out["errors"][:8]:
        print("   err:", e)
    return 0 if out["bars"] else 1


def cmd_coverage(args):
    conn = S.connect()
    now = time.time()
    rows = S.coverage(conn)
    if not rows:
        print("no samples yet")
        return 0
    print("%-16s %6s  %-20s %-20s %8s  %s" % ("series", "rows", "first", "last", "age", "session"))
    for r in rows:
        spec = C.BY_ID.get(r["series_id"], {})
        sess = spec.get("session", "?")
        print("%-16s %6d  %-20s %-20s %8s  %s"
              % (r["series_id"], r["n"], _fmt_ts(r["first"]), _fmt_ts(r["last"]),
                 "%dm" % int((now - r["last"]) / 60), sess))
    bars = {}
    for b in conn.execute("SELECT interval, COUNT(*) n FROM bar GROUP BY interval"):
        bars[b["interval"]] = b["n"]
    print("bars:", bars or "none")
    return 0


def cmd_health(args):
    from .sources import biquote, brsapi, others
    print("biquote :", biquote.health())
    print("brsapi  :", brsapi.health())
    for prov in ("wallex", "swissquote", "coingecko", "erapi"):
        fn = others.PROVIDERS.get(prov)
        if fn is None:
            others.load_providers()
            fn = others.PROVIDERS.get(prov)
        sym = {"wallex": "USDTTMN", "swissquote": "XAU/USD", "coingecko": "bitcoin",
               "erapi": "CNY"}.get(prov, "XAU/USD")
        t0 = time.time()
        try:
            q = fn(sym)
            print("%-8s: ok %4d ms  %s=%s" % (prov, (time.time() - t0) * 1000, sym, q["value"]))
        except Exception as e:                              # noqa: BLE001
            print("%-8s: FAIL %s" % (prov, str(e)[:90]))
    print("\nsessions:")
    for s in sorted({x["session"] for x in C.SERIES}):
        print("  %-10s %-9s %s" % (s, M.state(s, time.time()), M.describe(s)))
    return 0


def cmd_prune(args):
    conn = S.connect()
    print(json.dumps(S.prune(conn)))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="irx")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    ig = sub.add_parser("ingest")
    ig.add_argument("what", choices=("intl", "iran", "all"), default="all", nargs="?")
    ig.add_argument("--no-crosscheck", action="store_true")
    ig.add_argument("--dry", action="store_true")
    ig.set_defaults(fn=cmd_ingest)

    bf = sub.add_parser("backfill")
    bf.add_argument("--intervals", default="5m,15m,1h")
    bf.add_argument("--count", type=int, default=500)
    bf.set_defaults(fn=cmd_backfill)

    sub.add_parser("coverage").set_defaults(fn=cmd_coverage)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    sub.add_parser("prune").set_defaults(fn=cmd_prune)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
