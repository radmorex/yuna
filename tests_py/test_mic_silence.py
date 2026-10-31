import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import mic_silence


class TestPhraseEnded(unittest.TestCase):
    def test_silence_after_speech_ends_phrase(self):
        self.assertTrue(mic_silence.phrase_ended("[Parsed_silencedetect_0 @ 0x79] silence_start: 1.992687"))

    def test_initial_silence_waits_for_speech(self):
        self.assertFalse(mic_silence.phrase_ended("[Parsed_silencedetect_0 @ 0x79] silence_start: 0"))

    def test_other_lines_are_ignored(self):
        self.assertFalse(mic_silence.phrase_ended("[Parsed_silencedetect_0 @ 0x79] silence_end: 3.0 | silence_duration: 3.0"))
        self.assertFalse(mic_silence.phrase_ended("Stream #0:0: Audio: pcm_s16le"))


if __name__ == "__main__":
    unittest.main()
