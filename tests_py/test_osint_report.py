import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import arsenal
import osint_report


class ReportTest(unittest.TestCase):
    def test_domain_summary(self):
        summary, text = osint_report.render("domain", {
            "domain": "example.com",
            "dns": {"A": ["1.2.3.4"], "NS": ["hera.ns.cloudflare.com."], "DNSSEC": True},
            "whois": {"creation_date": "1995-08-14 04:00:00+00:00", "registrar": "IANA"},
            "ssl": {"days_left": 55, "verified": True},
        })
        self.assertEqual(summary, "example.com: DNS у cloudflare.com, зареєстрований у 1995, сертифікат ще 55 днів.")
        self.assertIn("A: 1.2.3.4", text)
        self.assertNotIn("{", text)

    def test_ip_limits_ports_in_voice_line(self):
        summary, text = osint_report.render("ip", {
            "ip": "1.1.1.1",
            "ipinfo": {"org": "Cloudflare", "city": "Brisbane", "country": "AU", "anycast": True},
            "internetdb": {"ports": [53, 69, 80, 443, 2053, 2082, 8080], "vulns": ["CVE-1"]},
        })
        self.assertIn("порти 53, 69, 80, 443, 2053 і ще 2", summary)
        self.assertIn("відомих вразливостей 1", summary)
        self.assertIn("8080", text)
        self.assertIn("anycast", text)

    def test_headers_lists_missing_protection(self):
        summary, text = osint_report.render("headers", {
            "redirects": [{"status": 301, "url": "http://a.com/"}, {"status": 200, "url": "https://a.com/"}],
            "headers": {"Server": "nginx", "Strict-Transport-Security": "max-age=1"},
        })
        self.assertIn("код 200", summary)
        self.assertIn("редиректів 1", summary)
        self.assertIn("немає 3 із 4", summary)
        self.assertIn("CSP", text)

    def test_wayback_reports_rate_limit(self):
        _, text = osint_report.render("wayback", {"url": "a.com", "snapshots": [], "last": "2026-10-29 06:35", "list_error": "429"})
        self.assertIn("обмежив частоту", text)
        summary, _ = osint_report.render("wayback", {"url": "a.com", "snapshots": []})
        self.assertIn("нічого не знайшла", summary)

    def test_reputation_verdict(self):
        bad, _ = osint_report.render("hash", {"hash": "abc", "virustotal": {"last_analysis_stats": {"malicious": 2, "suspicious": 1, "harmless": 7}}})
        self.assertIn("3 із 10", bad)
        clean, _ = osint_report.render("urlcheck", {"url": "https://a.com", "virustotal": {"last_analysis_stats": {"malicious": 0, "harmless": 5}}})
        self.assertIn("загроз не знайдено", clean)
        unknown, _ = osint_report.render("urlcheck", {"url": "https://a.com", "virustotal": {"error": "x"}})
        self.assertIn("не дав оцінки", unknown)

    def test_unexpected_shape_does_not_crash(self):
        self.assertEqual(osint_report.render("ip", "текст")[1], "текст")
        summary, _ = osint_report.render("domain", {"dns": "зламано"})
        self.assertTrue(summary)


class BatchTest(unittest.TestCase):
    def test_targets_are_validated(self):
        self.assertEqual(arsenal.batch_targets("A.com a.com 8.8.8.8"), ["a.com", "8.8.8.8"])
        for bad in ("a.com", "a.com 192.168.1.1", "a.com -x", " ".join(f"s{i}.com" for i in range(11))):
            with self.assertRaises(arsenal.Refused):
                arsenal.batch_targets(bad)

    def test_one_failure_does_not_stop_the_rest(self):
        def fake(tool, target):
            if target == "b.com":
                raise arsenal.Refused("немає зв'язку")
            return {"domain": target, "dns": {"NS": ["ns.host.net."]}}

        with mock.patch.object(arsenal, "run_osint", fake), mock.patch.object(arsenal, "BATCH_PAUSE", 0):
            summary, text, full = arsenal.run_batch(["a.com", "b.com", "c.com"])
        self.assertIn("2 із 3", summary)
        self.assertIn("b.com: не вийшло", text)
        self.assertIn("## c.com", full)


class HostTest(unittest.TestCase):
    def resolves_to(self, address):
        return mock.patch.object(arsenal.socket, "getaddrinfo", return_value=[(2, 1, 6, "", (address, 0))])

    def test_domain_pointing_inside_is_refused(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fe80::1%en0"):
            with self.resolves_to(address):
                for tool, target in (("headers", "http://localtest.me:8787/health"), ("domain", "localtest.me"), ("wayback", "localtest.me"), ("page", "https://localtest.me/")):
                    with self.assertRaises(arsenal.Refused, msg=f"{tool} {address}"):
                        arsenal.check_target(tool, target)

    def test_public_and_unresolved_pass(self):
        with self.resolves_to("93.184.216.34"):
            arsenal.check_target("headers", "https://example.com/")
        with mock.patch.object(arsenal.socket, "getaddrinfo", side_effect=OSError):
            arsenal.check_target("domain", "example.com")

    def test_batch_refuses_only_the_inside_target(self):
        def resolve(host, _):
            return [(2, 1, 6, "", ("127.0.0.1" if host == "localtest.me" else "93.184.216.34", 0))]

        with mock.patch.object(arsenal.socket, "getaddrinfo", resolve), mock.patch.object(arsenal, "BATCH_PAUSE", 0), \
                mock.patch.object(arsenal, "run_osint", lambda tool, target: {"domain": target, "dns": {"NS": ["ns.host.net."]}}):
            summary, text, _ = arsenal.run_batch(["example.com", "localtest.me"])
        self.assertIn("1 із 2", summary)
        self.assertIn("localtest.me: не вийшло", text)


class FailureTest(unittest.TestCase):
    def test_real_reason_is_shown(self):
        self.assertNotIn("URLSCAN", arsenal.explain_failure("Ошибка: Нужен ключ VirusTotal", ""))
        self.assertIn("OSINT_URLSCAN", arsenal.explain_failure("Ошибка: Нужен ключ VirusTotal или urlscan.io", ""))
        self.assertIn("сховище", arsenal.explain_failure("Ошибка: hdiutil attach failed", ""))
        self.assertIn("немає DNS", arsenal.explain_failure("Ошибка: немає DNS", ""))


if __name__ == "__main__":
    unittest.main()
