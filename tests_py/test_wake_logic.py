import array
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import wake_logic as w


def tone(frames, level=0.2):
    n = w.FRAME_BYTES // 2
    return [array.array("h", [int(level * 32767 * math.sin(i / 3)) for i in range(n)]).tobytes()] * frames


def quiet(frames):
    return [bytes(w.FRAME_BYTES)] * frames


def run(frames):
    seg = w.Segmenter()
    return [p for p in (seg.feed(f) for f in frames) if p]


class TestAfterWake(unittest.TestCase):
    def test_command_after_name(self):
        self.assertEqual(w.after_wake("Юна, відкрий ютуб."), "відкрий ютуб.")
        self.assertEqual(w.after_wake(" Юно увімкни музику"), "увімкни музику")
        self.assertEqual(w.after_wake("Йуна, як справи"), "як справи")

    def test_name_alone(self):
        self.assertEqual(w.after_wake("Юна."), "")

    def test_other_speech_is_not_for_her(self):
        for text in ["Юля, відкрий двері.", "Сьогодні гарна погода, Юна.", "Юнак пішов", "", None]:
            self.assertIsNone(w.after_wake(text), text)


class TestSegmenter(unittest.TestCase):
    def test_phrase_ends_after_silence(self):
        phrases = run(quiet(30) + tone(40) + quiet(30))
        self.assertEqual(len(phrases), 1)
        self.assertGreaterEqual(len(phrases[0]), 40 * w.FRAME_BYTES)

    def test_click_is_not_a_phrase(self):
        self.assertEqual(run(quiet(30) + tone(2) + quiet(30)), [])

    def test_silence_gives_nothing(self):
        self.assertEqual(run(quiet(200)), [])

    def test_endless_sound_is_cut(self):
        self.assertTrue(run(tone(w.MAX_PHRASE_FRAMES + 5)))


if __name__ == "__main__":
    unittest.main()
