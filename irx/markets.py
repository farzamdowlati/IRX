"""irx/markets.py — session engine: is this market open, and when does it next change?

Two sources of truth, in this order:
  1. a declared schedule (this module) — per-market hours in the market's OWN
     timezone via zoneinfo, so DST is handled by the tz database, not by
     hand-maintained UTC offsets;
  2. observed freshness (ingest/analysis) — if the feed's own quote time has not
     advanced, the market is closed whatever the schedule says. That is how
     holidays and emergency closures resolve themselves with no calendar.

Weekday numbering below is Python's: Mon=0 ... Sat=5, Sun=6.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

# Each rule: kind = always | utc_weekly | local_windows
SESSION_RULES = {
    "crypto": {"kind": "always", "desc": "24/7"},

    # Spot FX: opens Sunday 21:00 UTC, closes Friday 21:00 UTC, continuous between.
    "fx": {"kind": "utc_weekly", "open_wd": 6, "open": "21:00", "close_wd": 4, "close": "21:00",
           "desc": "Sun 21:00 - Fri 21:00 UTC"},

    # Futures (metals/energy): Sunday 22:00 UTC to Friday 21:00 UTC, daily halt 21:00-22:00 UTC.
    "futures": {"kind": "utc_weekly", "open_wd": 6, "open": "22:00", "close_wd": 4, "close": "21:00",
                "daily_break": ("21:00", "22:00"),
                "desc": "Sun 22:00 - Fri 21:00 UTC (daily halt 21:00-22:00 UTC)"},

    # Iran: measured from IRX's own 286 stored samples (docs/RESHAPE-PLAN.md §2).
    # Sat-Wed 11:00-19:30, Thursday short day 11:00-16:30, Friday closed, all Tehran time.
    "iran": {"kind": "local_windows", "tz": "Asia/Tehran",
             "windows": {5: [("11:00", "19:30")],
                         6: [("11:00", "19:30")],
                         0: [("11:00", "19:30")],
                         1: [("11:00", "19:30")],
                         2: [("11:00", "19:30")],
                         3: [("11:00", "16:30")],
                         4: []},
             "desc": "Sat-Wed 11:00-19:30, Thu 11:00-16:30 Tehran (measured), Fri closed"},

    "cash:US": {"kind": "local_windows", "tz": "America/New_York",
                "windows": {0: [("09:30", "16:00")], 1: [("09:30", "16:00")],
                            2: [("09:30", "16:00")], 3: [("09:30", "16:00")],
                            4: [("09:30", "16:00")]},
                "desc": "Mon-Fri 09:30-16:00 New York"},
    "cash:JP": {"kind": "local_windows", "tz": "Asia/Tokyo",
                "windows": {0: [("09:00", "11:30"), ("12:30", "15:00")],
                            1: [("09:00", "11:30"), ("12:30", "15:00")],
                            2: [("09:00", "11:30"), ("12:30", "15:00")],
                            3: [("09:00", "11:30"), ("12:30", "15:00")],
                            4: [("09:00", "11:30"), ("12:30", "15:00")]},
                "desc": "Mon-Fri 09:00-11:30 / 12:30-15:00 Tokyo"},
    "cash:HK": {"kind": "local_windows", "tz": "Asia/Hong_Kong",
                "windows": {0: [("09:30", "12:00"), ("13:00", "16:00")],
                            1: [("09:30", "12:00"), ("13:00", "16:00")],
                            2: [("09:30", "12:00"), ("13:00", "16:00")],
                            3: [("09:30", "12:00"), ("13:00", "16:00")],
                            4: [("09:30", "12:00"), ("13:00", "16:00")]},
                "desc": "Mon-Fri 09:30-12:00 / 13:00-16:00 Hong Kong"},
    "cash:CN": {"kind": "local_windows", "tz": "Asia/Shanghai",
                "windows": {0: [("09:30", "11:30"), ("13:00", "15:00")],
                            1: [("09:30", "11:30"), ("13:00", "15:00")],
                            2: [("09:30", "11:30"), ("13:00", "15:00")],
                            3: [("09:30", "11:30"), ("13:00", "15:00")],
                            4: [("09:30", "11:30"), ("13:00", "15:00")]},
                "desc": "Mon-Fri 09:30-11:30 / 13:00-15:00 Shanghai"},
    "cash:KR": {"kind": "local_windows", "tz": "Asia/Seoul",
                "windows": {w: [("09:00", "15:30")] for w in range(5)},
                "desc": "Mon-Fri 09:00-15:30 Seoul"},
    "cash:TW": {"kind": "local_windows", "tz": "Asia/Taipei",
                "windows": {w: [("09:00", "13:30")] for w in range(5)},
                "desc": "Mon-Fri 09:00-13:30 Taipei"},
    "cash:IN": {"kind": "local_windows", "tz": "Asia/Kolkata",
                "windows": {w: [("09:15", "15:30")] for w in range(5)},
                "desc": "Mon-Fri 09:15-15:30 Mumbai"},
    "cash:DE": {"kind": "local_windows", "tz": "Europe/Berlin",
                "windows": {w: [("09:00", "17:30")] for w in range(5)},
                "desc": "Mon-Fri 09:00-17:30 Frankfurt"},
    "cash:UK": {"kind": "local_windows", "tz": "Europe/London",
                "windows": {w: [("08:00", "16:30")] for w in range(5)},
                "desc": "Mon-Fri 08:00-16:30 London"},
    "cash:AU": {"kind": "local_windows", "tz": "Australia/Sydney",
                "windows": {w: [("10:00", "16:00")] for w in range(5)},
                "desc": "Mon-Fri 10:00-16:00 Sydney"},
}

_TZ_CACHE: dict[str, ZoneInfo | None] = {}


def _tz(name: str):
    if name not in _TZ_CACHE:
        try:
            _TZ_CACHE[name] = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            _TZ_CACHE[name] = None
    return _TZ_CACHE[name]


def _hm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


def _within(windows, local_dt) -> bool:
    mins = local_dt.hour * 60 + local_dt.minute
    for a, b in windows:
        ah, am = _hm(a)
        bh, bm = _hm(b)
        if ah * 60 + am <= mins < bh * 60 + bm:
            return True
    return False


def schedule_open(session: str, ts: float) -> bool:
    """Is the declared schedule open at ts (epoch seconds)?"""
    rule = SESSION_RULES.get(session)
    if rule is None:
        return True
    kind = rule["kind"]
    if kind == "always":
        return True

    dt = datetime.fromtimestamp(ts, UTC)

    if kind == "utc_weekly":
        wd = dt.weekday()
        open_wd, close_wd = rule["open_wd"], rule["close_wd"]
        # weekly window expressed in UTC clock terms
        oh, om = _hm(rule["open"])
        ch, cm = _hm(rule["close"])
        mins = dt.hour * 60 + dt.minute
        # normalise the week so Sat=5..Sun=6 ordering does not confuse the span test
        # window: [open_wd:open] -> [close_wd:close], wrapping through the week
        span_min = ((close_wd - open_wd) % 7) * 1440 + (ch * 60 + cm) - (oh * 60 + om)
        into = ((wd - open_wd) % 7) * 1440 + mins - (oh * 60 + om)
        if not (0 <= into < span_min):
            return False
        brk = rule.get("daily_break")
        if brk:
            ba, bb = _hm(brk[0]), _hm(brk[1])
            if ba[0] * 60 + ba[1] <= mins < bb[0] * 60 + bb[1]:
                return False
        return True

    if kind == "local_windows":
        tz = _tz(rule["tz"])
        if tz is None:                      # no tz data -> do not claim closed
            return True
        local = datetime.fromtimestamp(ts, tz)
        return _within(rule.get("windows", {}).get(local.weekday(), []), local)

    return True


def next_change(session: str, ts: float, max_days: int = 8):
    """(opening_at|None, closes_at|None) as epoch seconds, minute resolution.

    Scans forward one minute at a time with an early exit; ~11k cheap calls worst
    case, measured well under 50 ms.
    """
    cur = schedule_open(session, ts)
    step = 60
    limit = int(ts + max_days * 86400)
    t = int(ts)
    while t < limit:
        t += step
        if schedule_open(session, t) != cur:
            if cur:
                return (None, t)     # currently open -> this is the close
            return (t, None)         # currently closed -> this is the open
    return (None, None)


def state(session: str, ts: float) -> str:
    return "open" if schedule_open(session, ts) else "closed"


def describe(session: str) -> str:
    return SESSION_RULES.get(session, {}).get("desc", "24/7")


def closed_note(session: str, ts: float) -> str:
    """Short human note for a closed market, e.g. 'closed, opens in 3h05m'."""
    opens, closes = next_change(session, ts)
    if opens:
        d = int(opens - ts)
        return "closed, opens in %dh%02dm" % (d // 3600, (d % 3600) // 60)
    return "closed"
