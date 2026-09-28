"""irx/bot.py — the single always-on process (python-telegram-bot 22).

It owns everything: sampling both cadences, the derived-series math, the hourly
edit, pages on demand, and alerts. There is no cron and no second daemon — the v1
listener is absorbed here (and its systemd unit must be disabled: two long-pollers
on one bot token make Telegram return 409 Conflict to both).

  run:  python3 -m irx.bot            (systemd unit: deploy/irx-bot.service)

Three things learned the hard way and encoded below:
  * PTB 22 AWAITS every job callback: a plain `def` job raises
    "object NoneType can't be used in 'await' expression" and the job is recorded as
    failed even though its body ran. All jobs here are `async def`, and the blocking
    stdlib HTTP/DB work is pushed to a thread with asyncio.to_thread so the event
    loop keeps polling.
  * Bot methods are coroutines. Calling them from a sync helper returns an un-awaited
    coroutine that silently does nothing — so send_page/broadcast are async.
  * concurrent_updates(True), because PTB's default of 1 serialises every update
    behind the slowest handler (the bug that made the sibling bot feel dead).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
                      Update)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, JobQueue)

from . import config as C, ingest, store as S
from .analysis import alerts
from .render import imagetable
from .render import pages as P

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s",
                    level=logging.INFO)
log = logging.getLogger("irx")

BUTTONS = {
    P.PAGE_PRICES: [("📈 OHLC", "page:ohlc"), ("🔀 Cross-market", "page:cross"),
                    ("📉 Trend 24h", "page:trend"), ("🔄 Refresh", "refresh")],
    P.PAGE_OHLC: [("📊 Prices", "page:prices"), ("🔀 Cross-market", "page:cross"),
                  ("📉 Trend 24h", "page:trend"), ("🔄 Refresh", "refresh")],
    P.PAGE_CROSS: [("📊 Prices", "page:prices"), ("📈 OHLC", "page:ohlc"),
                   ("📉 Trend 24h", "page:trend"), ("🔄 Refresh", "refresh")],
    P.PAGE_TREND: [("📊 Prices", "page:prices"), ("📈 OHLC", "page:ohlc"),
                   ("🔀 Cross-market", "page:cross"), ("🔄 Refresh", "refresh")],
}


def keyboard(page: str) -> InlineKeyboardMarkup:
    rows = BUTTONS.get(page, BUTTONS[P.PAGE_PRICES])
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in rows]])


def whitelisted(conn) -> list:
    return [c["chat_id"] for c in S.chats(conn)]


def migrate_whitelist(conn) -> int:
    """Import the v1 data/whitelist.json once, so the rewrite does not lock the
    existing subscribers (and the owner) out.

    The path comes from the CONNECTION's database file, not the configured default:
    assuming the default silently read the production file while a test (or a second
    instance) pointed somewhere else.
    """
    import os
    path = os.path.join(os.path.dirname(S.db_path(conn)), "whitelist.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as f:
            ids = json.load(f)
    except Exception:                                        # noqa: BLE001
        return 0
    known = {c["chat_id"] for c in S.chats(conn, active_only=False)}
    n = 0
    for cid in ids:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        if cid not in known:
            S.upsert_chat(conn, cid, kind="owner" if cid == C.OWNER_CHAT_ID else "user")
            n += 1
    return n


# ------------------------------------------------------------------ blocking work
def _do(conn, fn):
    """Run a blocking DB/HTTP section with the store lock held (PTB hands us worker
    threads, and the sqlite connection is shared)."""
    with S.lock:
        return fn(conn)


def work_intl(conn):
    out = ingest.run_intl(conn)
    n = ingest.record_gaps(conn)
    return out, n


def work_iran(conn):
    out = ingest.run_iran(conn)
    ingest.record_gaps(conn)
    return out


# --------------------------------------------------------------------- sending
async def _delete(bot, chat_id: int, msg_id: int | None) -> None:
    if not msg_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception as e:                                   # noqa: BLE001
        log.info("could not delete message %s in %s: %s", msg_id, chat_id, e)


async def send_page(bot, conn, chat_id: int, page: str, now: float,
                    force: bool = False) -> str:
    """Create or edit this chat's one message. Returns 'sent' | 'edited' | 'skipped'.

    The OHLC page goes out as an IMAGE: six full-width columns cannot stay a table at
    a phone's monospace width, so rows wrap and the columns collapse (the user's
    complaint, verbatim). Telegram cannot turn a text message into a photo or back,
    so when the page type changes the new message is sent FIRST and the old one
    deleted only after it succeeds — never the other way round, or a failed send
    would leave the chat with nothing.

    The quiet-hours signature is computed from the text page even when an image is
    about to be sent, so the "nothing changed" test stays data-based and is not
    defeated by the timestamp drawn inside the picture.
    """
    def prep():
        with S.lock:
            ch = S.get_chat(conn, chat_id)
            if not ch:
                # A chat with no row would take our stored msg_id into nothing, and
                # every later call would post a NEW message instead of editing.
                S.upsert_chat(conn, chat_id)
                ch = S.get_chat(conn, chat_id) or {}
            state = {}
            if ch.get("state_json"):
                try:
                    state = json.loads(ch["state_json"])
                except (ValueError, TypeError):
                    state = {}
            sig = P.signature(conn, page, now)
            if P.all_closed(conn, page, now) and state.get("sig") == sig and not force:
                return {"action": "skipped"}
            media = imagetable.render_ohlc(conn, now) if page == P.PAGE_OHLC else None
            text = P.ohlc_caption(conn, now) if media else P.render(conn, page, now)
            return {"action": "go", "text": text, "kb": keyboard(page),
                    "msg_id": ch.get("msg_id"), "media": media,
                    "was_media": bool(state.get("media")), "sig": sig}

    plan = await asyncio.to_thread(prep)
    if plan["action"] == "skipped":
        return "skipped"

    def remember(msg_id=None, media=None):
        with S.lock:
            st = {"sig": plan["sig"], "page": page, "media": bool(media)}
            S.set_chat(conn, chat_id, page=page, state_json=json.dumps(st))
            if msg_id:
                S.set_chat(conn, chat_id, msg_id=msg_id)

    if plan["media"] is not None:
        if plan["msg_id"] and plan["was_media"]:
            try:
                await bot.edit_message_media(
                    chat_id=chat_id, message_id=plan["msg_id"],
                    media=InputMediaPhoto(plan["media"], caption=plan["text"],
                                          parse_mode=ParseMode.HTML),
                    reply_markup=plan["kb"])
                remember(media=True)
                return "edited"
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    remember(media=True)
                    return "skipped"
                log.warning("media edit failed for %s (%s); sending a fresh photo",
                            chat_id, e)
        msg = await bot.send_photo(chat_id=chat_id, photo=plan["media"],
                                   caption=plan["text"], reply_markup=plan["kb"],
                                   parse_mode=ParseMode.HTML)
        await _delete(bot, chat_id, plan["msg_id"])
        remember(msg.message_id, media=True)
        return "sent"

    if plan["msg_id"] and plan["was_media"]:
        # text cannot be edited into media: post the text, then drop the old photo
        msg = await bot.send_message(chat_id=chat_id, text=plan["text"],
                                     reply_markup=plan["kb"], parse_mode=ParseMode.HTML,
                                     disable_web_page_preview=True)
        await _delete(bot, chat_id, plan["msg_id"])
        remember(msg.message_id, media=False)
        return "sent"

    if plan["msg_id"]:
        try:
            await bot.edit_message_text(text=plan["text"], chat_id=chat_id,
                                        message_id=plan["msg_id"],
                                        reply_markup=plan["kb"],
                                        parse_mode=ParseMode.HTML)
            remember(media=False)
            return "edited"
        except BadRequest as e:
            if "not modified" in str(e).lower():
                remember(media=False)
                return "skipped"
            log.warning("edit failed for %s (%s); sending a fresh message", chat_id, e)
    msg = await bot.send_message(chat_id=chat_id, text=plan["text"],
                                 reply_markup=plan["kb"], parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True)
    remember(msg.message_id, media=False)
    return "sent"


async def broadcast(bot, conn, text: str) -> int:
    with S.lock:
        cids = whitelisted(conn)
    n = 0
    for cid in cids:
        try:
            await bot.send_message(chat_id=cid, text=text, parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
            n += 1
        except Exception as e:                               # noqa: BLE001
            log.warning("broadcast to %s failed: %s", cid, e)
    return n


# ----------------------------------------------------------------------- jobs
async def job_intl(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = S.connect()
    out, n_gaps = await asyncio.to_thread(_do, conn, work_intl)
    log.info("intl: ok=%d fail=%d crosschecks=%d gaps=%d", out["ok"], len(out["fail"]),
             out["crosschecks"], n_gaps)
    if out["fail"]:
        log.warning("intl failures: %s", [f["id"] for f in out["fail"]])


async def job_iran(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = S.connect()
    out = await asyncio.to_thread(_do, conn, work_iran)
    log.info("iran: ok=%d fail=%d frozen=%s", out["ok"], len(out["fail"]),
             out.get("src_frozen"))


async def job_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hourly: refresh every chat's current page in place. Telegram edits do not
    notify, so an unchanged quiet page is silent by construction."""
    conn = S.connect()
    with S.lock:
        chats = S.chats(conn)
    results: dict = {}
    for ch in chats:
        page = ch.get("page") or P.PAGE_PRICES
        try:
            r = await send_page(context.bot, conn, ch["chat_id"], page, time.time())
        except Exception as e:                               # noqa: BLE001
            log.warning("report to %s failed: %s", ch["chat_id"], e)
            continue
        results[r] = results.get(r, 0) + 1
    log.info("report: %s", results or "no chats")


async def job_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = S.connect()
    fired = await asyncio.to_thread(_do, conn, lambda c: alerts.evaluate(c, time.time()))
    if not fired:
        return
    for a in fired:
        text = "⚠️ <b>IRX alert</b> · %s\n%s" % (a["kind"], a["detail"])
        if await broadcast(context.bot, conn, text):
            with S.lock:
                S.mark_alert_sent(conn, a["id"])
    log.info("alerts: %d fired", len(fired))


async def job_maintenance(context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = S.connect()
    pruned = await asyncio.to_thread(_do, conn, lambda c: S.prune(c))
    log.info("maintenance: %s", pruned)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("handler error", exc_info=context.error)


# ------------------------------------------------------------------- commands
_HOLD = ("IRX is a private watchlist. I have passed your id to the owner; you will "
         "get the panel once it is approved.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    conn = S.connect()
    with S.lock:
        ch = S.get_chat(conn, cid)
        if not ch:
            if cid != C.OWNER_CHAT_ID:
                await update.message.reply_text(_HOLD)
                if C.OWNER_CHAT_ID:
                    await context.bot.send_message(
                        C.OWNER_CHAT_ID,
                        "🙋 access request from chat <code>%d</code> — approve with "
                        "<code>/add %d</code>" % (cid, cid),
                        parse_mode=ParseMode.HTML)
                return
            S.upsert_chat(conn, cid, kind="owner")
        S.set_chat(conn, cid, active=1, msg_id=None)
        page = (S.get_chat(conn, cid) or {}).get("page") or P.PAGE_PRICES
    await send_page(context.bot, conn, cid, page, time.time(), force=True)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with S.lock:
        S.set_chat(S.connect(), update.effective_chat.id, active=0)
    await update.message.reply_text("Stopped. Send /start to resume.")


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    conn = S.connect()
    await asyncio.to_thread(_do, conn, lambda c: (ingest.run_intl(c), ingest.run_iran(c),
                                                  ingest.record_gaps(c)))
    page = (S.get_chat(conn, cid) or {}).get("page") or P.PAGE_PRICES
    r = await send_page(context.bot, conn, cid, page, time.time(), force=True)
    log.info("manual refresh by %s -> %s", cid, r)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != C.OWNER_CHAT_ID:
        return
    for tok in context.args:
        try:
            target = int(tok)
        except ValueError:
            continue
        with S.lock:
            S.upsert_chat(S.connect(), target)
        await update.message.reply_text("added %d" % target)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != C.OWNER_CHAT_ID:
        return
    for tok in context.args:
        try:
            target = int(tok)
        except ValueError:
            continue
        with S.lock:
            S.set_chat(S.connect(), target, active=0)
        await update.message.reply_text("removed %d" % target)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != C.OWNER_CHAT_ID:
        return
    with S.lock:
        rows = S.chats(S.connect(), active_only=False)
    lines = ["<b>chats</b>"] + ["%d · %s · %s" % (r["chat_id"], r["kind"],
                                                  "active" if r["active"] else "off")
                                for r in rows]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ------------------------------------------------------------------- callbacks
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    cid = update.effective_chat.id
    data = q.data or ""
    conn = S.connect()
    if data == "refresh":
        await asyncio.to_thread(_do, conn, lambda c: (ingest.run_intl(c),
                                                      ingest.run_iran(c),
                                                      ingest.record_gaps(c)))
        page = (S.get_chat(conn, cid) or {}).get("page") or P.PAGE_PRICES
    elif data.startswith("page:"):
        page = data.split(":", 1)[1]
        page = page if page in P.PAGE_ORDER else P.PAGE_PRICES
    else:
        page = P.PAGE_PRICES
    try:
        await send_page(context.bot, conn, cid, page, time.time(), force=(data == "refresh"))
    except Exception as e:                                   # noqa: BLE001
        log.warning("button %r for %s failed: %s", data, cid, e)


# ------------------------------------------------------------------ bootstrap
def build() -> Application:
    if not C.BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing from .env")
    app = (Application.builder().token(C.BOT_TOKEN)
           .concurrent_updates(True)      # default is 1: one slow handler blocks all
           .build())
    jq: JobQueue = app.job_queue
    jq.run_repeating(job_intl, interval=C.INTL_INTERVAL_MIN * 60, first=5, name="intl")
    jq.run_repeating(job_iran, interval=C.IRAN_INTERVAL_MIN * 60, first=15, name="iran")
    jq.run_repeating(job_alerts, interval=C.ALERT_EVERY_SEC, first=60, name="alerts")
    jq.run_repeating(job_report, interval=C.REPORT_INTERVAL_MIN * 60, first=20,
                     name="report")
    jq.run_daily(job_maintenance, time=dt.time(3, 30), name="prune")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    conn = S.connect()
    with S.lock:
        S.init(conn)
        imported = migrate_whitelist(conn)
    if imported:
        log.info("imported %d chat(s) from the v1 whitelist", imported)
    app = build()
    log.info("IRX bot starting: %d chat(s)", len(whitelisted(S.connect())))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
