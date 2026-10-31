import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discord_bot import logic  # noqa: E402


class TestShouldRespond(unittest.TestCase):
    def test_triggers(self):
        for t in ["Юна, привет", "юна, как дела?", "ЮНА! ау", "Юна: что думаешь",
                  "  Юна,  тест", "Yuna, hi", "yuna! yo", "Юна", "Юна?", "Юна... ну"]:
            self.assertTrue(logic.should_respond(t, False), t)

    def test_non_triggers(self):
        for t in ["Миранда, привет", "мирамистин", "привет юна,", "Юна привет", "", "Mirage, hi",
                  "мир, труд"]:
            self.assertFalse(logic.should_respond(t, False), t)

    def test_mention(self):
        self.assertTrue(logic.should_respond("что угодно", True))

    def test_custom_names(self):
        self.assertTrue(logic.should_respond("Бот, привет", False, ["бот"]))


class TestStripTrigger(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(logic.strip_trigger("Юна, привет!"), "привет!")
        self.assertEqual(logic.strip_trigger("yuna:  как дела"), "как дела")
        self.assertEqual(logic.strip_trigger("<@123456> расскажи"), "расскажи")
        self.assertEqual(logic.strip_trigger("<@!123>, Юна, тест"), "тест")
        self.assertEqual(logic.strip_trigger("Юна"), "")
        self.assertEqual(logic.strip_trigger("просто текст"), "просто текст")


class TestIsAdmin(unittest.TestCase):
    def test_user(self):
        self.assertTrue(logic.is_admin(42, [], {"42"}, set()))
        self.assertTrue(logic.is_admin("42", [], [42], []))

    def test_role(self):
        self.assertTrue(logic.is_admin(1, [7, 9], set(), {"9"}))
        self.assertFalse(logic.is_admin(1, [7], set(), {"9"}))

    def test_empty(self):
        self.assertFalse(logic.is_admin(1, [1], set(), set()))
        self.assertFalse(logic.is_admin(None, None, [""], [""]))


class TestRateLimiter(unittest.TestCase):
    def test_window(self):
        now = [0.0]
        rl = logic.RateLimiter(max_events=2, window=10, clock=lambda: now[0])
        self.assertTrue(rl.allow("u"))
        self.assertTrue(rl.allow("u"))
        self.assertFalse(rl.allow("u"))
        self.assertTrue(rl.allow("other"))
        now[0] = 9.9
        self.assertFalse(rl.allow("u"))
        now[0] = 10.0
        self.assertTrue(rl.allow("u"))

    def test_bad_args(self):
        with self.assertRaises(ValueError):
            logic.RateLimiter(0, 1)


class TestMask(unittest.TestCase):
    def test_mask(self):
        tok = "MTIzNDU2Nzg5.abcdef.secretpart1234"
        m = logic.mask_token(tok)
        self.assertNotIn("secret", m)
        self.assertTrue(m.endswith("1234"))
        self.assertEqual(logic.mask_token(""), "<пусто>")
        self.assertEqual(logic.mask_token("short"), "****")

    def test_mask_url(self):
        self.assertEqual(logic.mask_url_token("ws://h/bridge?token=abc&x=1"), "ws://h/bridge?token=****&x=1")

    def test_config_repr_hides_secrets(self):
        cfg = logic.BotConfig.from_env({"DISCORD_TOKEN": "supersecrettokenvalue", "BRIDGE_TOKEN": "bridgesecret99"})
        r = repr(cfg)
        self.assertNotIn("supersecret", r)
        self.assertNotIn("bridgesecret", r)


class TestBotNames(unittest.TestCase):
    def test_from_env(self):
        self.assertEqual(logic.bot_names_from_env({"BOT_NAME": "Кира", "BOT_ALIASES": "Kira, Кирочка"}), ("кира", "kira", "кирочка"))
        self.assertEqual(logic.bot_names_from_env({}), logic.DEFAULT_BOT_NAMES)
        self.assertTrue(logic.should_respond("Кира, привет", False, ("кира",)))
        self.assertFalse(logic.should_respond("Мира, привет", False))


class TestBridge(unittest.TestCase):
    def test_build_event(self):
        ev = logic.build_bridge_event(user="Vasya", text="привет", is_admin=True,
                                      channel_id=123, message_id=456, mentioned=False)
        self.assertEqual(ev, {"type": "chat", "platform": "discord", "user": "Vasya", "text": "привет",
                              "isAdmin": True, "channelId": "123", "messageId": "456", "mentioned": False})
        json.dumps(ev)
        ev2 = logic.build_bridge_event(user="V", text="t", is_admin=False, channel_id=1, message_id=2,
                                       mentioned=False, user_id=777)
        self.assertEqual(ev2["userId"], "777")

    def test_parse_ok(self):
        r = logic.parse_bridge_message(json.dumps({"type": "reply", "channelId": "123",
                                                   "replyToMessageId": "456", "text": "хай",
                                                   "audioUrl": "/audio/abc.wav"}))
        self.assertEqual((r.channel_id, r.reply_to_message_id, r.text, r.audio_url),
                         ("123", "456", "хай", "/audio/abc.wav"))
        r2 = logic.parse_bridge_message({"type": "reply", "channelId": 1, "replyToMessageId": None,
                                         "text": "x", "audioUrl": None})
        self.assertIsNone(r2.audio_url)
        self.assertIsNone(r2.reply_to_message_id)

    def test_parse_bad(self):
        bad = ["not json", "[]", json.dumps({"type": "chat"}),
               json.dumps({"type": "reply", "channelId": "abc", "text": "x"}),
               json.dumps({"type": "reply", "channelId": "1", "text": ""}),
               json.dumps({"type": "reply", "channelId": "1", "text": "x", "replyToMessageId": "zz"}),
               json.dumps({"type": "reply", "channelId": "1", "text": "x", "audioUrl": "file:///etc/passwd"}),
               json.dumps({"type": "reply", "channelId": "1", "text": 5})]
        for b in bad:
            with self.assertRaises(logic.BridgeMessageError, msg=b):
                logic.parse_bridge_message(b)

    def test_resolve_audio(self):
        core = "http://127.0.0.1:8787"
        self.assertEqual(logic.resolve_audio_url(core, "/audio/a-1.wav"), "http://127.0.0.1:8787/audio/a-1.wav")
        self.assertIsNone(logic.resolve_audio_url(core, "/audio/../secret.wav"))
        self.assertIsNone(logic.resolve_audio_url(core, "http://evil.example/audio/a.wav"))
        self.assertEqual(logic.resolve_audio_url(core, "http://127.0.0.1:8787/audio/x.wav"),
                         "http://127.0.0.1:8787/audio/x.wav")

    def test_ws_url(self):
        self.assertEqual(logic.bridge_ws_url("http://127.0.0.1:8787", "a b&c"),
                         "ws://127.0.0.1:8787/bridge?token=a%20b%26c")
        self.assertTrue(logic.bridge_ws_url("https://x", "t").startswith("wss://"))

    def test_backoff(self):
        g = logic.backoff_delays(1, 8)
        self.assertEqual([next(g) for _ in range(6)], [1, 2, 4, 8, 8, 8])


class TestChunk(unittest.TestCase):
    def test_short(self):
        self.assertEqual(logic.chunk_text("abc"), ["abc"])
        self.assertEqual(logic.chunk_text(""), [])

    def test_long(self):
        text = ("слово " * 1000).strip()
        parts = logic.chunk_text(text)
        self.assertTrue(all(len(p) <= 2000 for p in parts))
        self.assertEqual(" ".join(parts), text)

    def test_no_spaces(self):
        parts = logic.chunk_text("x" * 4500, 2000)
        self.assertEqual([len(p) for p in parts], [2000, 2000, 500])


class TestEnv(unittest.TestCase):
    def test_parse(self):
        env = logic.parse_env(
            "# comment\nDISCORD_TOKEN=abc\nexport CORE_URL=\"http://x:1\"\n"
            "A='q # not comment'\nB=val # comment\nbad line\n1X=no\nEMPTY=\n")
        self.assertEqual(env, {"DISCORD_TOKEN": "abc", "CORE_URL": "http://x:1",
                               "A": "q # not comment", "B": "val", "EMPTY": ""})

    def test_load_file_does_not_override(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write("X1=file\nX2=file\n")
        try:
            environ = {"X1": "env"}
            logic.load_env_file(f.name, environ)
            self.assertEqual(environ, {"X1": "env", "X2": "file"})
            self.assertEqual(logic.load_env_file("/nonexistent/.env", {}), {})
        finally:
            os.unlink(f.name)

    def test_config(self):
        cfg = logic.BotConfig.from_env({
            "DISCORD_TOKEN": " t ", "DISCORD_ADMIN_USER_IDS": "1, 2 x", "DISCORD_ADMIN_ROLE_IDS": "3",
            "DISCORD_CHANNEL_IDS": "10;11", "DISCORD_VOICE_ENABLED": "1", "DISCORD_VOICE_CHANNEL_ID": "99",
            "CORE_URL": "http://h:1/"})
        self.assertEqual(cfg.token, "t")
        self.assertEqual(cfg.admin_user_ids, {"1", "2"})
        self.assertEqual(cfg.channel_ids, {"10", "11"})
        self.assertTrue(cfg.voice_enabled)
        self.assertEqual(cfg.voice_channel_id, "99")
        self.assertEqual(cfg.core_url, "http://h:1")
        self.assertTrue(cfg.channel_allowed(10))
        self.assertFalse(cfg.channel_allowed(12))
        d = logic.BotConfig.from_env({})
        self.assertEqual(d.core_url, "http://127.0.0.1:8787")
        self.assertFalse(d.voice_enabled)
        self.assertTrue(d.channel_allowed(5))


if __name__ == "__main__":
    unittest.main()
