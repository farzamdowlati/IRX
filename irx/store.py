"""irx/store.py — SQLite persistence: samples, bars, chats, alerts, meta.

Why SQLite and not a time-series DB: at our volume (~7,200 rows/day) an extra
daemon buys nothing, while SQLite gives indexed range scans, one-writer
semantics and atomic bot-state updates with zero install (stdlib, tr ships
3.37.2). Rationale and schema in docs/RESHAPE-PLAN.md §5.
"""
from __future__ import annotations

import os
import sqlite3
import time

from . import config as C

SCHEMA_VERSION = "2"

DDL = """
CREATE TABLE IF NOT EXISTS series (
  id TEXT PRIMARY KEY, kind TEXT, source TEXT, tz TEXT, session TEXT, unit TEXT, label TEXT
);
CREATE TABLE IF NOT EXISTS sample (
  series_id TEXT NOT NULL, ts INTEGER NOT NULL, value REAL, src_ts INTEGER,
  source TEXT, stale INTEGER DEFAULT 0,
  PRIMARY KEY (series_id, ts)
);
CREATE INDEX IF NOT EXISTS sample_ts ON sample(ts);
CREATE TABLE IF NOT EXISTS bar (
  series_id TEXT NOT NULL, interval TEXT NOT NULL, open_ts INTEGER NOT NULL,
  o REAL, h REAL, l REAL, c REAL, n INTEGER, src TEXT,
  PRIMARY KEY (series_id, interval, open_ts)
);
CREATE TABLE IF NOT EXISTS chat (
  chat_id INTEGER PRIMARY KEY, kind TEXT DEFAULT 'user', added_ts INTEGER,
  msg_id INTEGER, page TEXT DEFAULT 'prices', anchor TEXT, state_json TEXT,
  active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alert (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, series_id TEXT,
  detail TEXT, sent INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_conn: sqlite3.Connection | None = None


def connect(path: str | None = None, fresh: bool = False) -> sqlite3.Connection:
    """Process-wide connection (WAL, busy timeout). Pass path=None for the default."""
    global _conn
    if fresh or _conn is None or path is not None:
        p = path or C.DB_PATH
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        c = sqlite3.connect(p, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        init(c)
        if path is None:
            _conn = c
        return c
    return _conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.executemany(
        "INSERT INTO series(id, kind, source, tz, session, unit, label) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, source=excluded.source, "
        "session=excluded.session, unit=excluded.unit, label=excluded.label",
        [(s["id"], s["group"], (s["sources"][0][0] if s["sources"] else None), None,
          s["session"], s["unit"], s["label"]) for s in C.SERIES])
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()


# ------------------------------------------------------------------ samples
def record(conn, series_id: str, value: float | None, ts: int | None = None,
           src_ts: int | None = None, source: str | None = None,
           stale: bool = False) -> bool:
    """Insert/replace one observation. Returns False for a None value."""
    if value is None:
        return False
    ts = int(ts or time.time())
    conn.execute(
        "INSERT INTO sample(series_id, ts, value, src_ts, source, stale) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(series_id, ts) DO UPDATE SET value=excluded.value, "
        "src_ts=COALESCE(excluded.src_ts, src_ts), source=excluded.source, stale=excluded.stale",
        (series_id, ts, float(value), int(src_ts) if src_ts else None, source, 1 if stale else 0))
    conn.commit()
    return True


def latest(conn, series_id: str):
    r = conn.execute("SELECT * FROM sample WHERE series_id=? ORDER BY ts DESC LIMIT 1",
                     (series_id,)).fetchone()
    return dict(r) if r else None


def latest_all(conn) -> dict:
    rows = conn.execute(
        "SELECT s.* FROM sample s JOIN (SELECT series_id, MAX(ts) mts FROM sample GROUP BY series_id) m "
        "ON s.series_id=m.series_id AND s.ts=m.mts").fetchall()
    return {r["series_id"]: dict(r) for r in rows}


def at_or_before(conn, series_id: str, ts: float):
    r = conn.execute("SELECT * FROM sample WHERE series_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                     (series_id, int(ts))).fetchone()
    return dict(r) if r else None


def window(conn, series_id: str, since: float, until: float | None = None) -> list:
    q = "SELECT ts, value, src_ts, source, stale FROM sample WHERE series_id=? AND ts>=?"
    args = [series_id, int(since)]
    if until is not None:
        q += " AND ts<=?"
        args.append(int(until))
    return [dict(r) for r in conn.execute(q + " ORDER BY ts", args).fetchall()]


def prev_close(conn, series_id: str, before_ts: float):
    """Last value before `before_ts` — used for 'since previous close' deltas."""
    return at_or_before(conn, series_id, before_ts)


def stats(conn, series_id: str, since: float) -> dict:
    r = conn.execute(
        "SELECT COUNT(*) n, MIN(value) lo, MAX(value) hi FROM sample WHERE series_id=? AND ts>=?",
        (series_id, int(since))).fetchone()
    return {"n": r["n"] or 0, "lo": r["lo"], "hi": r["hi"]}


def coverage(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT series_id, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM sample GROUP BY series_id "
        "ORDER BY series_id").fetchall()]


# --------------------------------------------------------------------- bars
def put_bar(conn, series_id: str, interval: str, open_ts: int,
            o=None, h=None, l=None, c=None, n=None, src=None) -> None:
    conn.execute(
        "INSERT INTO bar(series_id, interval, open_ts, o, h, l, c, n, src) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(series_id, interval, open_ts) DO UPDATE SET "
        "o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c, n=excluded.n, src=excluded.src",
        (series_id, interval, int(open_ts), o, h, l, c, n, src))


def get_bars(conn, series_id: str, interval: str, since: float | None = None,
             until: float | None = None, limit: int = 500) -> list:
    q = "SELECT * FROM bar WHERE series_id=? AND interval=?"
    args = [series_id, interval]
    if since is not None:
        q += " AND open_ts>=?"
        args.append(int(since))
    if until is not None:
        q += " AND open_ts<=?"
        args.append(int(until))
    rows = [dict(r) for r in conn.execute(q + " ORDER BY open_ts DESC LIMIT ?",
                                          args + [limit]).fetchall()]
    return list(reversed(rows))


def commit(conn) -> None:
    conn.commit()


# -------------------------------------------------------------------- chats
def upsert_chat(conn, chat_id: int, kind: str = "user", active: int = 1) -> None:
    conn.execute("INSERT INTO chat(chat_id, kind, added_ts, active) VALUES(?,?,?,?) "
                 "ON CONFLICT(chat_id) DO UPDATE SET active=excluded.active",
                 (int(chat_id), kind, int(time.time()), active))
    conn.commit()


def set_chat(conn, chat_id: int, **fields) -> None:
    allowed = {"msg_id", "page", "anchor", "state_json", "active", "kind"}
    kv = {k: v for k, v in fields.items() if k in allowed}
    if not kv:
        return
    sets = ", ".join("%s=?" % k for k in kv)
    conn.execute("UPDATE chat SET %s WHERE chat_id=?" % sets,
                 list(kv.values()) + [int(chat_id)])
    conn.commit()


def get_chat(conn, chat_id: int):
    r = conn.execute("SELECT * FROM chat WHERE chat_id=?", (int(chat_id),)).fetchone()
    return dict(r) if r else None


def chats(conn, active_only: bool = True) -> list:
    q = "SELECT * FROM chat" + (" WHERE active=1" if active_only else "")
    return [dict(r) for r in conn.execute(q + " ORDER BY added_ts").fetchall()]


# ------------------------------------------------------------------- alerts
def add_alert(conn, kind: str, series_id: str | None, detail: str,
              ts: int | None = None) -> int:
    cur = conn.execute("INSERT INTO alert(ts, kind, series_id, detail) VALUES(?,?,?,?)",
                       (int(ts or time.time()), kind, series_id, detail))
    conn.commit()
    return cur.lastrowid


def recent_alert(conn, kind: str, series_id: str | None, since: float):
    if series_id is None:
        r = conn.execute("SELECT * FROM alert WHERE kind=? AND ts>=? ORDER BY ts DESC LIMIT 1",
                         (kind, int(since))).fetchone()
    else:
        r = conn.execute(
            "SELECT * FROM alert WHERE kind=? AND series_id=? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (kind, series_id, int(since))).fetchone()
    return dict(r) if r else None


def unsent_alerts(conn) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM alert WHERE sent=0 ORDER BY ts").fetchall()]


def mark_alert_sent(conn, alert_id: int) -> None:
    conn.execute("UPDATE alert SET sent=1 WHERE id=?", (int(alert_id),))
    conn.commit()


# --------------------------------------------------------------------- meta
def set_meta(conn, k: str, v) -> None:
    conn.execute("INSERT INTO meta(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, str(v)))
    conn.commit()


def get_meta(conn, k: str, default=None):
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def bump_failures(conn, series_id: str) -> int:
    k = "fail:%s" % series_id
    n = int(get_meta(conn, k, "0") or 0) + 1
    set_meta(conn, k, n)
    return n


def clear_failures(conn, series_id: str) -> None:
    set_meta(conn, "fail:%s" % series_id, 0)


def failure_count(conn, series_id: str) -> int:
    return int(get_meta(conn, "fail:%s" % series_id, "0") or 0)


# ------------------------------------------------------------------ pruning
def prune(conn, now: float | None = None) -> dict:
    """Retention: raw international 90d, Iranian 400d, 1d bars forever, intraday bars 120d."""
    now = now or time.time()
    cut_intl = int(now - 90 * 86400)
    cut_iran = int(now - 400 * 86400)
    intl = [s["id"] for s in C.SERIES if s["cadence"] == "intl"]
    iran = [s["id"] for s in C.SERIES if s["cadence"] == "iran"]
    out = {}
    if intl:
        q = "DELETE FROM sample WHERE ts<? AND series_id IN (%s)" % ",".join("?" * len(intl))
        out["sample_intl"] = conn.execute(q, [cut_intl] + intl).rowcount
    if iran:
        q = "DELETE FROM sample WHERE ts<? AND series_id IN (%s)" % ",".join("?" * len(iran))
        out["sample_iran"] = conn.execute(q, [cut_iran] + iran).rowcount
    out["bar"] = conn.execute("DELETE FROM bar WHERE interval<>'1d' AND open_ts<?",
                              (int(now - 120 * 86400),)).rowcount
    out["alert"] = conn.execute("DELETE FROM alert WHERE ts<?", (int(now - 60 * 86400),)).rowcount
    conn.commit()
    return out
