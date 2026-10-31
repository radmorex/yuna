import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import arsenal


class TestValidate(unittest.TestCase):
    def test_accepts_targets(self):
        self.assertEqual(arsenal.validate("domain", "Example.com"), "Example.com")
        self.assertEqual(arsenal.validate("ip", "8.8.8.8"), "8.8.8.8")
        self.assertEqual(arsenal.validate("links", "https://example.com/a"), "https://example.com/a")
        self.assertEqual(arsenal.validate("wayback", "example.com"), "example.com")
        self.assertTrue(arsenal.validate("hash", "d41d8cd98f00b204e9800998ecf8427e"))

    def test_refuses_bad_targets(self):
        for tool, target in [("domain", "-rf"), ("domain", "localhost"), ("ip", "127.0.0.1"), ("ip", "10.0.0.1"),
                             ("ip", "169.254.169.254"), ("links", "file:///etc/passwd"), ("links", "http://127.0.0.1/"),
                             ("links", "http://user:pw@example.com/"), ("links", "http://192.168.1.1/x"), ("hash", "abc"), ("web", "a\nb")]:
            with self.assertRaises(arsenal.Refused, msg=f"{tool} {target}"):
                arsenal.validate(tool, target)

    def test_no_people_search_tools(self):
        for tool in ("username", "telegram", "wp", "name", "email", "phone"):
            self.assertNotIn(tool, arsenal.OSINT)
            self.assertNotIn(tool, arsenal.WEB_TOOLS)
            with self.assertRaises(arsenal.Refused):
                arsenal.validate(tool, "kurasaki")

    def test_vpn_check_fails_closed(self):
        on = '{"state": "connected", "details": {"location": {"mullvad_exit_ip": true}}}'
        self.assertTrue(arsenal.vpn_confirmed(0, on))
        self.assertFalse(arsenal.vpn_confirmed(1, on))
        self.assertFalse(arsenal.vpn_confirmed(0, on.replace("true", "false")))
        self.assertFalse(arsenal.vpn_confirmed(0, '{"state": "disconnected"}'))
        self.assertFalse(arsenal.vpn_confirmed(0, 'не json'))
        self.assertFalse(arsenal.vpn_confirmed(0, '[]'))

    def test_short_limits_output(self):
        self.assertLessEqual(len(arsenal.short("x" * 5000)), arsenal.LIMIT + 60)
        snaps = {"snapshots": [{"date": "2001"}, {"date": "2026"}]}
        self.assertIn("2026", arsenal.short(snaps))


if __name__ == "__main__":
    unittest.main()
