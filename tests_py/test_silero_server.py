import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
import wave

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import silero_server  # noqa: E402


class TestSanitize(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(silero_server.sanitize_text("Привет 😀 <b>мир</b>!"), "Привет b мир b !")
        self.assertEqual(silero_server.sanitize_text("Мне 21 год"), "Мне двадцать один год")
        self.assertEqual(silero_server.sanitize_text("2001"), "две тысячи один")
        self.assertEqual(silero_server.sanitize_text("1000"), "одна тысяча")

    def test_validate(self):
        v = silero_server.validate_request
        self.assertEqual(v({"text": "привет"}, "xenia"), ("привет", "xenia", 48000))
        for bad in [{"text": ""}, {"text": "а" * 1001}, {"text": "a", "speaker": "bob"},
                    {"text": "a", "sample_rate": 44100}, {"text": "a", "sample_rate": True},
                    {"text": "😀😀"}, [], {"text": 5}]:
            with self.assertRaises(silero_server.ValidationError, msg=bad):
                v(bad, "xenia")


class TestServerMock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = silero_server.make_server("127.0.0.1", 0, mock=True)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def post(self, body: bytes):
        req = urllib.request.Request(self.url("/tts"), data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=10)

    def test_health(self):
        with urllib.request.urlopen(self.url("/health"), timeout=5) as r:
            data = json.loads(r.read())
        self.assertTrue(data["ok"])
        self.assertTrue(data["mock"])

    def test_tts_wav(self):
        body = json.dumps({"text": "Привет, чат! Юна на связи.", "speaker": "baya", "sample_rate": 24000}).encode()
        with self.post(body) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "audio/wav")
            data = r.read()
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"WAVE")
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getnchannels(), 1)
            self.assertGreater(w.getnframes(), 1000)

    def test_validation_400(self):
        cases = [b"{not json", json.dumps({"text": ""}).encode(),
                 json.dumps({"text": "x" * 1001}).encode(),
                 json.dumps({"text": "hi", "speaker": "nobody"}).encode(),
                 json.dumps({"text": "hi", "sample_rate": 44100}).encode()]
        for c in cases:
            with self.assertRaises(urllib.error.HTTPError, msg=c[:40]) as cm:
                self.post(c)
            self.assertEqual(cm.exception.code, 400)
            err = json.loads(cm.exception.read())
            self.assertIn("error", err)
            cm.exception.close()

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url("/nope"), timeout=5)
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()


if __name__ == "__main__":
    unittest.main()
