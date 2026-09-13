#!/usr/bin/env python3
"""Shared helpers for irx weekly analytics. Stdlib only.

Reads data/history.jsonl (written by irx_brief.py) and formats plain-text
reports suitable for Telegram.
"""
import json
import math
import os
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import envcfg as E  # noqa: E402  (repo-root config loader)

TEHRAN = timezone(timedelta(hours=3, minutes=30))
GRAM_PER_OZ = 31.1034768
HISTORY = os.path.join(E.DATA_DIR, "history.jsonl")
TG_LIMIT = 3900  # Telegram hard-caps messages at 4096 chars


# ---------- data ----------

def load_rows(days=None, fields=()):
    """History rows, oldest first. `days` keeps a trailing window; `fields`
    drops rows missing any named key (protects against pre-v4 rows)."""
    if not os.path.exists(HISTORY):
        return []
    rows = []
    for line in open(HISTORY):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    rows.sort(key=lambda r: r.get("ts", 0))
    if days is not None and rows:
        cutoff = rows[-1]["ts"] - days * 86400
        rows = [r for r in rows if r["ts"] >= cutoff]
    if fields:
        rows = [r for r in rows if all(r.get(k) is not None for k in fields)]
    return rows


def tehran(ts):
    """Format a Unix timestamp as Tehran wall-clock."""
    return datetime.fromtimestamp(ts, TEHRAN)


# ---------- stats ----------

def sd(values):
    return st.pstdev(values) if len(values) > 1 else 0.0


def rets(values):
    """Plain fractional returns between consecutive observations."""
    return [values[i + 1] / values[i] - 1.0
            for i in range(len(values) - 1) if values[i]]


def log_rets(values):
    out = []
    for i in range(len(values) - 1):
        if values[i] and values[i + 1]:
            out.append(math.log(values[i + 1] / values[i]))
    return out


def corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if n < 2:
        return float("nan")
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else float("nan")


def acf1(values):
    """Lag-1 autocorrelation: momentum/trend indicator."""
    if len(values) < 3:
        return float("nan")
    m = st.mean(values)
    den = sum((x - m) ** 2 for x in values)
    return sum((values[i] - m) * (values[i + 1] - m)
               for i in range(len(values) - 1)) / den if den else float("nan")


def variance_ratio(values, q=5):
    """>1 trending, <1 mean-reverting, ~1 random walk."""
    lr = log_rets(values)
    n = len(lr) // q * q
    if n < 2 * q:
        return None
    agg = [sum(lr[i:i + q]) for i in range(0, n, q)]
    base = st.pvariance(lr)
    return st.pvariance(agg) / (q * base) if base else None


def pct(a, b):
    return (a / b - 1.0) * 100.0


# ---------- delivery ----------

def chunks(text, limit=TG_LIMIT):
    """Split on line boundaries so each part fits Telegram's message cap."""
    parts, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > limit:
            parts.append(buf.rstrip())
            buf = ""
        buf += line + "\n"
    if buf.strip():
        parts.append(buf.rstrip())
    return parts


def send_telegram(text, dry=False):
    """Broadcast to the whitelist; returns (sent, failed) chat ids."""
    if dry:
        print(text)
        return 0, 0
    token = E.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.stderr.write("[warn] no TELEGRAM_BOT_TOKEN; printing only\n")
        print(text)
        return 0, 0
    path = os.path.join(E.DATA_DIR, "whitelist.json")
    try:
        chats = sorted(json.load(open(path)))
    except Exception:
        owner = E.get_int("OWNER_CHAT_ID", 0)
        chats = [owner] if owner else []
    sent = failed = 0
    for cid in chats:
        try:
            for part in chunks(text):
                data = urllib.parse.urlencode({"chat_id": cid, "text": part,
                                               "disable_web_page_preview": "true"}).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=data, method="POST")
                with urllib.request.urlopen(req, timeout=25) as r:
                    if not json.loads(r.read().decode()).get("ok"):
                        raise RuntimeError("telegram returned ok=false")
            sent += 1
        except Exception as e:
            failed += 1
            sys.stderr.write(f"[warn] send to {cid} failed: {e}\n")
    return sent, failed


def stamp():
    return f"generated {tehran(time.time()):%a %d %b %Y %H:%M} Tehran"
