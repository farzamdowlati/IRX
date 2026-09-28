"""irx/analysis/alerts.py — push rules, with a cooldown per (rule, series).

Rules are deliberately few and each one names the exact condition it fired on, so
a notification is checkable rather than atmospheric:
  move     one-hour move beyond the per-session threshold (config.ALERT_MOVE_*)
  iran     a street/coin jump beyond ALERT_MOVE_IRAN_PCT (thin market, lower bar)
  cross    a Tier-A gap's robust z-score over its own 7-day distribution
  tether   USDT premium beyond its stress band
  outage   a required source failed N cycles in a row (the panel is going blind)

Every rule respects the cooldown recorded in the `alert` table, and nothing is
sent twice: the caller marks rows sent.
"""
from __future__ import annotations

import time

from .. import config as C, store as S
from . import cross

OUTAGE_CYCLES = 3


def _move_threshold(spec: dict) -> float:
    sess = spec.get("session", "")
    if spec.get("group") == "iran":
        return C.ALERT_MOVE_PCT["iran"]
    if sess in ("fx",):
        return C.ALERT_MOVE_PCT["fx"]
    if sess == "crypto":
        return C.ALERT_MOVE_PCT["crypto"]
    if sess.startswith("cash:"):
        return C.ALERT_INDEX_PCT
    return C.ALERT_MOVE_PCT["futures"]


def _cooled(conn, kind: str, series_id: str, now: float) -> bool:
    recent = S.recent_alert(conn, kind, series_id, now - C.ALERT_COOLDOWN_MIN * 60)
    return recent is None


def check_moves(conn, now: float | None = None) -> list:
    """One-hour percentage move per series, versus that series' own threshold."""
    now = now or time.time()
    out = []
    from .. import markets as M
    for spec in C.SERIES:
        sid = spec["id"]
        latest = S.latest(conn, sid)
        if not latest or latest["value"] is None:
            continue
        ref = S.at_or_before(conn, sid, now - 3600)
        if not ref or not ref["value"] or ref["ts"] < now - 3 * 3600:
            continue          # no real one-hour reference: stay quiet rather than
            #                   compare against a stale closed-market price
        pct = (latest["value"] / ref["value"] - 1) * 100.0
        thr = _move_threshold(spec)
        # A closed market cannot have "moved": skip when the session is shut, unless
        # the series is 24/7.
        if not M.schedule_open(spec["session"], now) and spec["session"] != "crypto":
            continue
        if abs(pct) >= thr and _cooled(conn, "move", sid, now):
            out.append({"kind": "move", "series_id": sid,
                        "detail": "%s %s %+.2f%% in 1h (threshold %.1f%%)"
                                  % (spec["label"], spec["id"], pct, thr),
                        "ts": int(now)})
    return out


def check_cross(conn, now: float | None = None, days: float = 7.0) -> list:
    """Tier-A gaps judged against their own 7-day robust distribution."""
    now = now or time.time()
    out = []
    for g in cross.parity_gaps(conn):
        sid = "gap:%s" % g["label"]
        hist = S.window(conn, sid, now - days * 86400, now)
        if len(hist) < 12:
            continue
        vals = [h["value"] for h in hist if h["value"] is not None]
        z = cross.robust_z(vals)
        if z is not None and abs(z) >= C.ALERT_Z and _cooled(conn, "cross", sid, now):
            out.append({"kind": "cross", "series_id": sid,
                        "detail": "%s gap %.2f%% is %.1f robust-sigma from its %.0f-day norm"
                                  % (g["label"], g["gap_pct"], z, days),
                        "ts": int(now)})
    return out


def check_tether(conn, now: float | None = None, high: float = 2.0, low: float = -1.0) -> list:
    now = now or time.time()
    p = cross.tether_premium(conn)
    if p is None:
        return []
    if (p >= high or p <= low) and _cooled(conn, "tether", "USDTIRT", now):
        return [{"kind": "tether", "series_id": "USDTIRT",
                 "detail": "USDT premium %+.2f%% over street USD (stress band %.1f%%/%.1f%%)"
                           % (p, high, low), "ts": int(now)}]
    return []


def check_outage(conn, now: float | None = None) -> list:
    now = now or time.time()
    out = []
    for spec in C.SERIES:
        n = S.failure_count(conn, spec["id"])
        if n >= OUTAGE_CYCLES and _cooled(conn, "outage", spec["id"], now):
            out.append({"kind": "outage", "series_id": spec["id"],
                        "detail": "%s has failed %d consecutive fetches — showing no value"
                                  % (spec["label"], n),
                        "ts": int(now)})
    return out


def evaluate(conn, now: float | None = None) -> list:
    """Run every rule, persist the new ones (marked unsent), return them for delivery."""
    now = now or time.time()
    fired = check_moves(conn, now) + check_cross(conn, now) + check_tether(conn, now) \
        + check_outage(conn, now)
    for a in fired:
        a["id"] = S.add_alert(conn, a["kind"], a["series_id"], a["detail"], ts=a["ts"])
    return fired
