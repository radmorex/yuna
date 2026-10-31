import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import transcribe


class TestCloudSettings(unittest.TestCase):
    def test_only_gemini_gets_audio(self):
        gemini = {"OPENAI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/", "OPENAI_API_KEY": "k", "OPENAI_MODEL": "m"}
        self.assertEqual(transcribe.cloud_settings(gemini), ("https://generativelanguage.googleapis.com/v1beta/openai", "k", "m"))
        self.assertIsNone(transcribe.cloud_settings({**gemini, "OPENAI_BASE_URL": "https://example.com/v1"}))
        self.assertIsNone(transcribe.cloud_settings({**gemini, "OPENAI_API_KEY": ""}))
        self.assertIsNone(transcribe.cloud_settings({}))

    def test_env_parsing(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write('# коментар=1\nA="b"\nC=d=e\n\n')
        try:
            self.assertEqual(transcribe.load_env(f.name), {"A": "b", "C": "d=e"})
        finally:
            os.unlink(f.name)
        self.assertEqual(transcribe.load_env("/nonexistent/.env"), {})

    def test_clean_text(self):
        self.assertEqual(transcribe.clean_cloud_text(' «Юна,  відкрий\nютуб» '.strip()), "Юна, відкрий ютуб")
        self.assertEqual(transcribe.clean_cloud_text(None), "")


if __name__ == "__main__":
    unittest.main()
