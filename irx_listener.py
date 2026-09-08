#!/usr/bin/env python3
"""
irx_listener.py — supervised whitelist for the brief bot (Telegram long-poll).

Access model:
- Owner (OWNER_CHAT_ID in .env) chats freely and runs:
    /add <chat_id>  approve        /remove <chat_id>  revoke
    /list           show whitelist /test  ping all subscribers
- Unknown chats get a hold notice (once) and the owner gets an access request
  with the numeric id to approve. Nobody can join the list by themselves.
- Whitelisted chats receive broadcasts; /stop unsubscribes.

State: data/whitelist.json (runtime, gitignored). Secrets: .env. Stdlib only.
Run under a process manager (see systemd unit in README).
"""
import json, os, sys, time, urllib.request, urllib.parse

import envcfg as E

STATE = os.path.join(E.DATA_DIR, "whitelist.json")
LAST_ID = os.path.join(E.DATA_DIR, "listener_offset")
NOTICED = set()
UA = {"User-Agent": "irx-listener/3.0"}

HELP = "Commands:\n/stop — unsubscribe from briefs\n/help — this message"
OWNER_HELP = ("/add <chat_id>  approve someone\n"
              "/remove <chat_id>  revoke access\n"
              "/list  current whitelist\n"
              "/test  ping all subscribers")


def api(method, token, **kw):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(kw).encode()
    req = urllib.request.Request(url, data=data, headers=UA, method="POST")
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.loads(r.read().decode())


def send(token, cid, text):
    try:
        if not api("sendMessage", token, chat_id=cid, text=text).get("ok"):
            return False
        return True
    except Exception as e:
        sys.stderr.write(f"[warn] send {cid}: {e}\n")
        return False


def load_wl(owner):
    try:
        w = sorted(json.load(open(STATE)))
    except Exception:
        w = [owner] if owner else []
        save_wl(w)
    if owner and owner not in w:
        w.append(owner); save_wl(w)
    return w


def save_wl(w):
    os.makedirs(E.DATA_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(sorted(set(int(x) for x in w)), open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def get_offset():
    try:
        return int(open(LAST_ID).read().strip())
    except Exception:
        return 0


def set_offset(n):
    os.makedirs(E.DATA_DIR, exist_ok=True)
    open(LAST_ID, "w").write(str(n))


def handle(owner, token, wl, msg):
    cid = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    name = " ".join(x for x in [msg["chat"].get("first_name") or "",
                                msg["chat"].get("last_name") or ""] if x) or "?"

    if cid == owner:
        p = text.split()
        if p and p[0].startswith("/add") and len(p) > 1:
            try:
                t = int(p[1])
            except ValueError:
                send(token, cid, "Usage: /add <numeric chat id>"); return
            if t not in wl:
                wl.append(t); save_wl(wl)
            send(token, cid, f"✅ Approved {t}.")
            send(token, t, "Approved by the owner — briefs arrive every "
                 f"{E.get_int('INTERVAL_MINUTES', 30)} min, "
                 f"{E.get_int('WINDOW_START', 7)}:00–{E.get_int('WINDOW_END', 22)}:00 Tehran. /stop to unsubscribe.")
        elif p and p[0].startswith("/remove") and len(p) > 1:
            try:
                t = int(p[1])
                if t in wl:
                    wl.remove(t); save_wl(wl)
                send(token, cid, f"Removed {t}.")
            except ValueError:
                send(token, cid, "Usage: /remove <chat id>")
        elif p and p[0].startswith("/test"):
            n = sum(1 for c in wl if send(token, c, "🔔 Test from the owner."))
            send(token, cid, f"Test sent to {n} subscribers.")
        elif text.startswith("/list") or text.startswith("/help") or text.startswith("/start"):
            send(token, cid, "Whitelist: " + ", ".join(map(str, wl)) + "\n\n" + OWNER_HELP)
        else:
            send(token, cid, OWNER_HELP)
        return

    if cid not in wl:
        if cid not in NOTICED:
            NOTICED.add(cid)
            send(token, cid, "🔒 This bot broadcasts only to people approved by its owner. I've let them know you're waiting.")
            send(token, owner, f"🙍 New access request: {name} (id: {cid})\nApprove with:\n/add {cid}")
        return

    if text.startswith("/stop"):
        if cid in wl:
            wl.remove(cid); save_wl(wl)
        send(token, cid, "Unsubscribed. The owner can re-add you with /add.")
        send(token, owner, f"⬅ {name} (id: {cid}) unsubscribed.")
    elif text.startswith("/help") or text.startswith("/start"):
        send(token, cid, HELP)
    else:
        send(token, cid, "You're subscribed ✅\n" + HELP)


def main():
    os.makedirs(E.DATA_DIR, exist_ok=True)
    token = E.get("TELEGRAM_BOT_TOKEN")
    owner = E.get_int("OWNER_CHAT_ID", 0)
    if not token or not owner:
        sys.stderr.write("set TELEGRAM_BOT_TOKEN and OWNER_CHAT_ID in .env\n"); sys.exit(2)
    wl = load_wl(owner)
    offset = get_offset()
    print("listener up", flush=True)
    while True:
        try:
            r = api("getUpdates", token, offset=offset + 1, timeout=50)
            for up in r.get("result", []):
                offset = max(offset, up["update_id"]); set_offset(offset)
                msg = up.get("message")
                if msg and (msg.get("text") or msg.get("caption")):
                    handle(owner, token, wl, msg)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            sys.stderr.write(f"[err] {e}\n"); time.sleep(5)


if __name__ == "__main__":
    main()
