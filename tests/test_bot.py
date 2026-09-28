"""Bot logic tests. The Telegram library is not on the dev python by default, so
these run under the PTB venv and SKIP cleanly elsewhere:

    /root/.irx/venv/bin/python tests/test_bot.py         (on tr)
    ~/.hermes/cache/scratch/ptbvenv/bin/python tests/test_bot.py

Nothing here touches the network: the bot object is a fake that records calls.
"""
import asyncio
import datetime
import inspect
import os
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import telegram  # noqa: F401
    from telegram.error import BadRequest
    HAVE_PTB = True
except Exception:                                            # noqa: BLE001
    HAVE_PTB = False

from irx import store as S


class FakeMessage:
    _next = 100

    def __init__(self):
        FakeMessage._next += 1
        self.message_id = FakeMessage._next


class FakeBot:
    """Async like the real Bot: PTB's send/edit are coroutines, and calling them
    without awaiting silently does nothing."""

    def __init__(self, not_modified=False):
        self.sent, self.edits, self.photos, self.media_edits, self.deleted = [], [], [], [], []
        self.not_modified = not_modified

    async def send_message(self, **kw):
        self.sent.append(kw)
        return FakeMessage()

    async def edit_message_text(self, **kw):
        if self.not_modified:
            raise BadRequest("Message is not modified")
        self.edits.append(kw)
        return True

    async def send_photo(self, **kw):
        self.photos.append(kw)
        return FakeMessage()

    async def edit_message_media(self, **kw):
        if self.not_modified:
            raise BadRequest("Message is not modified")
        self.media_edits.append(kw)
        return True

    async def delete_message(self, chat_id=None, message_id=None):
        self.deleted.append((chat_id, message_id))
        return True


FRIDAY_NIGHT = datetime.datetime(2026, 10, 2, 20, 0, tzinfo=ZoneInfo("UTC")).timestamp()


@unittest.skipUnless(HAVE_PTB, "python-telegram-bot not installed on this interpreter")
class TestBotLogic(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.c = S.connect(self.path)
        # a plain weekday midday, so no quiet-hours rule is in play by accident
        # (1.8e9 epoch is a FRIDAY, which silently made three tests take the
        # closed-market path)
        self.now = datetime.datetime(2026, 9, 30, 12, 0,
                                     tzinfo=ZoneInfo("UTC")).timestamp()
        for sid, v in (("USDIRT", 244_215), ("USDTIRT", 243_688), ("WTI", 92.88),
                       ("BTCUSD", 83_276), ("XAUUSD", 4135.72), ("G18", 24_432_100)):
            S.record(self.c, sid, v, ts=self.now, source="test")

    def tearDown(self):
        self.c.close()
        os.unlink(self.path)

    # ------------------------------------------------- the async contract itself
    def test_every_job_is_a_coroutine_function(self):
        """Regression: PTB 22 does `await self.callback(context)`, so a plain sync job
        raises "object NoneType can't be used in 'await' expression" and is recorded
        as failed even though its body ran (hit live on the first deploy)."""
        from irx import bot
        for name in ("job_intl", "job_iran", "job_report", "job_alerts",
                     "job_maintenance"):
            self.assertTrue(inspect.iscoroutinefunction(getattr(bot, name)),
                            "%s must be async def" % name)

    def test_ptb_bot_methods_really_are_coroutines(self):
        from telegram import Bot
        self.assertTrue(asyncio.iscoroutinefunction(Bot.send_message))
        self.assertTrue(asyncio.iscoroutinefunction(Bot.edit_message_text))

    def test_handlers_are_coroutine_functions(self):
        from irx import bot
        for name in ("cmd_start", "cmd_stop", "cmd_refresh", "on_button", "on_error"):
            self.assertTrue(inspect.iscoroutinefunction(getattr(bot, name)), name)

    # ------------------------------------------------------------- keyboards
    def test_every_page_offers_the_other_pages_and_a_way_back(self):
        from irx.bot import BUTTONS
        from irx.render import pages as P
        for page in P.PAGE_ORDER:
            datas = [d for _t, d in BUTTONS[page]]
            self.assertIn("refresh", datas)
            if page != P.PAGE_PRICES:
                self.assertIn("page:prices", datas)          # the Back button
            self.assertLessEqual(len(datas), 4)              # one row, no crowding
            for _t, d in BUTTONS[page]:
                self.assertLessEqual(len(d.encode()), 64)    # telegram callback cap

    def test_callback_data_only_names_real_pages(self):
        from irx.bot import BUTTONS
        from irx.render import pages as P
        valid = {"refresh"} | {"page:%s" % p for p in P.PAGE_ORDER}
        for _page, btns in BUTTONS.items():
            for _t, d in btns:
                self.assertIn(d, valid)

    # --------------------------------------------------------- edit in place
    async def test_send_page_sends_once_then_edits_the_same_message(self):
        from irx.bot import send_page
        bot = FakeBot()
        self.assertEqual(await send_page(bot, self.c, 42, "prices", self.now), "sent")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.edits, [])
        mid = S.get_chat(self.c, 42)["msg_id"]
        self.assertEqual(await send_page(bot, self.c, 42, "cross", self.now), "edited")
        self.assertEqual(len(bot.sent), 1)                   # still one message
        self.assertEqual(bot.edits[0]["message_id"], mid)
        self.assertEqual(S.get_chat(self.c, 42)["page"], "cross")

    async def test_not_modified_response_is_not_an_error(self):
        from irx.bot import send_page
        bot = FakeBot()
        await send_page(bot, self.c, 42, "prices", self.now)
        bot.not_modified = True
        self.assertEqual(await send_page(bot, self.c, 42, "prices", self.now), "skipped")

    async def test_force_bypasses_the_quiet_hours_skip(self):
        from irx.bot import send_page
        bot = FakeBot()
        await send_page(bot, self.c, 42, "prices", self.now)
        r = await send_page(bot, self.c, 42, "prices", self.now, force=True)
        self.assertIn(r, ("edited", "sent"))

    async def test_quiet_hours_skip_only_when_nothing_changed(self):
        """The skip means 'every displayed market closed AND the page byte-identical'.
        With Bitcoin on the page a quiet hour still shows BTC moving, so the condition
        is exercised directly instead of by emptying the DB."""
        from irx.bot import send_page
        from irx.render import pages as P
        real = P.all_closed
        P.all_closed = lambda conn, page, now: True
        try:
            bot = FakeBot()
            self.assertEqual(await send_page(bot, self.c, 42, "prices", self.now), "sent")
            calls = len(bot.sent) + len(bot.edits)
            self.assertEqual(await send_page(bot, self.c, 42, "prices", self.now),
                             "skipped")
            self.assertEqual(len(bot.sent) + len(bot.edits), calls)
            S.record(self.c, "USDIRT", 250_000, ts=self.now + 5, source="test")
            self.assertNotEqual(await send_page(bot, self.c, 42, "prices", self.now),
                                "skipped")
        finally:
            P.all_closed = real

    def test_all_closed_is_false_while_bitcoin_trades(self):
        from irx.render import pages as P
        self.assertFalse(P.all_closed(self.c, P.PAGE_PRICES, FRIDAY_NIGHT))

    # ------------------------------------------------------- the OHLC image page
    async def test_ohlc_page_is_delivered_as_a_photo(self):
        from irx.bot import send_page
        from irx import bot as botmod
        if not botmod.imagetable.available():
            self.skipTest("no image renderer on this host")
        bot = FakeBot()
        self.assertEqual(await send_page(bot, self.c, 7, "ohlc", self.now), "sent")
        self.assertEqual(len(bot.photos), 1)
        self.assertEqual(bot.sent, [])                      # image, not text
        self.assertTrue(bot.photos[0]["caption"].startswith("📈"))

    async def test_switching_between_text_and_photo_resends_and_cleans_up(self):
        """Telegram cannot edit a text message into a photo, so the type change must
        post the new message first and delete the old one after."""
        from irx.bot import send_page
        from irx import bot as botmod
        if not botmod.imagetable.available():
            self.skipTest("no image renderer on this host")
        bot = FakeBot()
        await send_page(bot, self.c, 7, "prices", self.now)          # text
        first = S.get_chat(self.c, 7)["msg_id"]
        self.assertEqual(await send_page(bot, self.c, 7, "ohlc", self.now), "sent")
        self.assertEqual(len(bot.photos), 1)
        self.assertIn((7, first), bot.deleted)              # old text cleaned up
        photo_id = S.get_chat(self.c, 7)["msg_id"]
        self.assertNotEqual(photo_id, first)
        # and back to text
        self.assertEqual(await send_page(bot, self.c, 7, "cross", self.now), "sent")
        self.assertEqual(len(bot.sent), 2)                  # prices + cross (2 texts)
        self.assertIn((7, photo_id), bot.deleted)

    async def test_edits_the_photo_in_place_when_already_a_photo(self):
        from irx.bot import send_page
        from irx import bot as botmod
        if not botmod.imagetable.available():
            self.skipTest("no image renderer on this host")
        bot = FakeBot()
        await send_page(bot, self.c, 7, "ohlc", self.now)
        S.record(self.c, "WTI", 123.45, ts=self.now, source="test")
        self.assertEqual(await send_page(bot, self.c, 7, "ohlc", self.now), "edited")
        self.assertEqual(len(bot.photos), 1)
        self.assertEqual(len(bot.media_edits), 1)
        self.assertEqual(bot.deleted, [])

    async def test_ohlc_falls_back_to_text_without_a_renderer(self):
        from irx.bot import send_page
        from irx import bot as botmod
        real = botmod.imagetable.render_ohlc
        botmod.imagetable.render_ohlc = lambda conn, now: None
        try:
            bot = FakeBot()
            await send_page(bot, self.c, 7, "ohlc", self.now)
            self.assertEqual(bot.photos, [])
            self.assertEqual(len(bot.sent), 1)
            self.assertIn("OHLC", bot.sent[0]["text"])
        finally:
            botmod.imagetable.render_ohlc = real

    # ------------------------------------------------------------- whitelist
    def test_whitelist_migration_imports_the_v1_file_once(self):
        import json
        from irx.bot import migrate_whitelist
        # fake ids only: the real owner chat id must never reach a public repo
        wl = os.path.join(os.path.dirname(self.path), "whitelist.json")
        with open(wl, "w") as f:
            json.dump([555000111, 999], f)
        try:
            self.assertEqual(migrate_whitelist(self.c), 2)
            self.assertEqual(migrate_whitelist(self.c), 0)   # idempotent
            ids = sorted(x["chat_id"] for x in S.chats(self.c))
            self.assertEqual(ids, [999, 555000111])
        finally:
            os.unlink(wl)

    def test_deactivated_chats_are_not_broadcast_to(self):
        from irx.bot import whitelisted
        S.upsert_chat(self.c, 1)
        S.upsert_chat(self.c, 2)
        S.set_chat(self.c, 2, active=0)
        self.assertEqual(whitelisted(self.c), [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
