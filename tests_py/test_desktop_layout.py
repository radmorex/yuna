import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import desktop_layout as layout


class TestDocking(unittest.TestCase):
    def test_nearest_corner(self):
        self.assertEqual(layout.nearest_corner(10, 40, False, 1920, 1080), "tl")
        self.assertEqual(layout.nearest_corner(1500, 40, False, 1920, 1080), "tr")
        self.assertEqual(layout.nearest_corner(10, 900, False, 1920, 1080), "bl")
        self.assertEqual(layout.nearest_corner(1500, 900, False, 1920, 1080), "br")

    def test_position_inside_screen(self):
        for compact in (False, True):
            w, h = layout.size(compact)
            for c in layout.CORNERS:
                x, y = layout.position(c, compact, 1440, 900)
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + w, 1440)
                self.assertLessEqual(y + h, 900)

    def test_snap_round_trip(self):
        for c in layout.CORNERS:
            x, y = layout.position(c, False, 1440, 900)
            self.assertEqual(layout.nearest_corner(x, y, False, 1440, 900), c)

    def test_moved(self):
        self.assertFalse(layout.moved(100, 100, 103, 98))
        self.assertTrue(layout.moved(100, 100, 120, 100))


if __name__ == "__main__":
    unittest.main()
