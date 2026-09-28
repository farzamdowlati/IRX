"""irx/http.py — one HTTP helper, with a PER-CALL user agent.

Per-call, never process-wide: the Iranian firewall bans a python user agent and
wants a browser string, while Cloudflare-fronted hosts do the opposite. Unifying
them breaks one of the two (learned the hard way, see the pipeline skill).
"""
from __future__ import annotations

import gzip
import json
import socket
import time
import urllib.error
import urllib.request

UA_BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
UA_HONEST = "irx/2.0 (personal market watchlist)"


class FetchError(RuntimeError):
    pass


def get(url: str, *, headers: dict | None = None, ua: str = UA_HONEST, timeout: int = 25,
        retries: int = 2, backoff: float = 1.5, raw: bool = False):
    """GET with retries. Returns parsed JSON (or text when raw=True)."""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": ua,
                                                  "Accept": "*/*",
                                                  "Accept-Encoding": "gzip"})
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
                text = body.decode("utf-8", "replace")
                return text if raw else json.loads(text)
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
                json.JSONDecodeError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(backoff ** attempt)
    raise FetchError("%s failed: %s" % (url.split("?")[0], last))
