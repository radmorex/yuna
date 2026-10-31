"""Модуль infra: домен, IP, хеш, URL, Web-Check (усі зовнішні API — локальні заглушки)."""

import socket

import pytest

from osint_kit.modules import infra

KEYS = {"shodan": "SHODAN-SECRET-1", "virustotal": "VT-SECRET-2", "abuseipdb": "ABUSE-SECRET-3",
        "urlscan": "URLSCAN-SECRET-4", "criminalip": "CIP-SECRET-5"}


@pytest.fixture
def fake_dns(monkeypatch):
    monkeypatch.setattr(infra, "dns_records", lambda d, t=10: {"A": ["93.184.216.34"], "NS": ["ns1.example.test"]})
    monkeypatch.setattr(infra, "whois_info", lambda d: {"registrar": "Test Registrar", "creation_date": "1995-08-14"})
    monkeypatch.setattr(infra, "ssl_cert", lambda h, p=443, t=10: {"issuer": "Test CA", "days_left": 90})
    monkeypatch.setattr(infra, "_txt", fake_txt)


CYMRU = {
    "34.216.184.93.origin.asn.cymru.com": ["15133 | 93.184.216.0/24 | US | arin | 2008-06-02"],
    "8.8.8.8.origin.asn.cymru.com": ["15169 | 8.8.8.0/24 | US | arin | 1992-12-01"],
    "71.212.134.45.origin.asn.cymru.com": ["212238 | 45.134.212.0/24 | GB | ripencc | 2019-07-29"],
    "AS15133.asn.cymru.com": ["15133 | US | arin | 2007-03-19 | EDGECAST, US"],
    "AS15169.asn.cymru.com": ["15169 | US | arin | 2000-03-30 | GOOGLE, US"],
    "AS212238.asn.cymru.com": ["212238 | GB | ripencc | 2020-12-02 | CDNEXT - Datacamp Limited, GB"],
}


def fake_txt(name, timeout=10):
    return CYMRU.get(name, [])


def test_domain(osint, server, fake_dns):
    osint.configure(keys={"virustotal": KEYS["virustotal"]})
    server.add(server.ext("example.test"), "<html>hi</html>",
               headers={"Strict-Transport-Security": "max-age=1", "Set-Cookie": "sid=1"})
    server.add(server.ext("example.test", "/robots.txt"), "User-agent: *\nDisallow: /admin", ctype="text/plain")
    server.add(server.ext("example.test", "/sitemap.xml"), "<html>soft 404</html>")
    server.add(server.ext("crt.sh"), [{"name_value": "*.example.test\nmail.example.test"},
                                      {"name_value": "MAIL.example.test"}, {"name_value": "evil.test"}])
    server.add(server.ext("ipinfo.io", "/93.184.216.34/json"), {"ip": "93.184.216.34", "org": "AS15133", "readme": "x"})
    server.add(server.ext("www.virustotal.com", "/api/v3/domains/example.test"),
               {"data": {"attributes": {"reputation": 5, "creation_date": 0}}})

    assert osint("infra", "domain", "https://Example.test/path") == 0
    [finding] = osint.findings()
    assert finding["target"] == "example.test"
    assert finding["data"].pop("сервер").startswith("BaseHTTP")
    assert finding["data"] == {"IP-адреса": ["93.184.216.34"], "хостинг": "AS15133 EDGECAST, US",
                               "CDN": "Edgecast — адреса принадлежат сети доставки, настоящий сервер сайта скрыт",
                               "регистратор": "Test Registrar", "создан": "14.08.1995", "NS": ["ns1.example.test"],
                               "SSL": "Test CA, осталось 90 дн.", "поддомены": ["mail.example.test"]}

    res = infra.domain(osint.cfg(), osint.case(), "example.test")
    assert res["subdomains"]["subdomains"] == ["example.test", "mail.example.test"]
    assert "strict-transport-security" not in res["http"]["security_headers_missing"]
    assert "content-security-policy" in res["http"]["security_headers_missing"]
    assert res["http"]["cookies_set"] == ["sid"]
    assert list(res["files"]) == ["/robots.txt"]  # sitemap віддав HTML -> відкинуто
    assert "readme" not in res["ip_hosting"]["93.184.216.34"]
    assert res["virustotal"]["creation_date"] == "1970-01-01T00:00:00+00:00"
    [vt_req] = server.hits(server.ext("www.virustotal.com", "/api/v3/domains/example.test"))[:1]
    assert vt_req["headers"]["x-apikey"] == KEYS["virustotal"]


def test_domain_without_subdomains(osint, server, fake_dns):
    res = infra.domain(osint.cfg(), osint.case(), "example.test", subdomains=False)
    assert "subdomains" not in res and "virustotal" not in res
    assert not server.hits(server.ext("crt.sh"))


def test_domain_dns_failure_is_recorded(osint, server, monkeypatch):
    # без заглушок dnspython/whois/ssl упираються в заборону мережі -> помилки, але команда працює
    res = infra.domain(osint.cfg(), osint.case(), "example.test", subdomains=False)
    assert "error" in res["whois"] and "error" in res["ssl"]
    data = osint.findings()[0]["data"]
    assert "IP-адреса" not in data and "whois" in " ".join(data["недоступно"])  # ошибки видны, а не спрятаны


def _ip_routes(server, ip="8.8.8.8"):
    server.add(server.ext("ipinfo.io", f"/{ip}/json"), {"ip": ip, "org": "AS15169 Google", "country": "US", "city": "X"})
    server.add(server.ext("internetdb.shodan.io", f"/{ip}"), {"ip": ip, "ports": [53, 443], "vulns": []})
    server.add(server.ext("api.shodan.io", f"/shodan/host/{ip}"),
               {"org": "Google", "ports": [53], "data": [{"port": 53, "transport": "udp", "data": "x" * 1000}]})
    server.add(server.ext("api.abuseipdb.com", "/api/v2/check"), {"data": {"abuseConfidenceScore": 0}})
    server.add(server.ext("api.criminalip.io", "/v1/asset/ip/report"),
               {"score": {"inbound": 1}, "port": {"data": [{"open_port_no": 53, "app_name": "dns"}]},
                "vulnerability": {"data": [{"cve_id": "CVE-1"}, {"cve_id": "CVE-1"}]}})
    server.add(server.ext("www.virustotal.com", f"/api/v3/ip_addresses/{ip}"),
               {"data": {"attributes": {"as_owner": "Google", "last_analysis_results": {
                   "EngineA": {"category": "malicious", "result": "phishing"},
                   "EngineB": {"category": "harmless", "result": "clean"}}}}})


def _ripestat(server, ip, record, abuse):
    server.add(server.ext("stat.ripe.net", "/data/whois/data.json"),
               {"data": {"records": [[{"key": k, "value": v} for k, v in record.items()]]}})
    server.add(server.ext("stat.ripe.net", "/data/abuse-contact-finder/data.json"), {"data": {"abuse_contacts": abuse}})


def test_ip(osint, server, monkeypatch):
    osint.configure(keys=KEYS)
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("dns.google", [], [ip]))
    monkeypatch.setattr(infra, "_txt", fake_txt)
    _ip_routes(server)
    _ripestat(server, "8.8.8.8", {"NetRange": "8.8.8.0 - 8.8.8.255", "NetName": "GOGL", "Country": "US"}, ["network-abuse@google.com"])
    assert osint("infra", "ip", "8.8.8.8") == 0
    [finding] = osint.findings()
    assert finding["data"] == {"провайдер": "AS15169 GOOGLE, US", "сеть": "GOGL", "диапазон": "8.8.8.0 - 8.8.8.255",
                               "страна": "US", "город": "X", "ptr": "dns.google", "abuse": "network-abuse@google.com",
                               "порты": [53, 443]}

    res = infra.ip(osint.cfg(), osint.case(), "8.8.8.8")
    assert len(res["shodan"]["services"][0]["banner"]) == 300
    assert res["criminalip"]["cves"] == ["CVE-1"]
    assert res["virustotal"]["flagged_by"] == {"EngineA": "phishing"}
    assert server.hits(server.ext("api.abuseipdb.com", "/api/v2/check"))[0]["headers"]["Key"] == KEYS["abuseipdb"]


def test_ip_behind_tor_blocks_still_gives_owner(osint, server, monkeypatch):
    """ipinfo и InternetDB отвечают 403 на выходы Tor — реестры (RIPEstat, Cymru) всё равно дают владельца сети."""
    ip = "45.134.212.71"
    monkeypatch.setattr(infra, "_txt", fake_txt)
    monkeypatch.setattr(infra, "reverse_dns", lambda a: {"ptr": "unn-45-134-212-71.datapacket.com"})
    server.add(server.ext("ipinfo.io", f"/{ip}/json"), "denied", status=403)
    server.add(server.ext("internetdb.shodan.io", f"/{ip}"), "denied", status=403)
    _ripestat(server, ip, {"inetnum": "45.134.212.0/24", "netname": "CDNEXT-WAR", "country": "PL"}, ["abuse@datacamp.co.uk"])
    infra.ip(osint.cfg(), osint.case(), ip)
    data = osint.findings()[0]["data"]
    assert data["провайдер"] == "AS212238 CDNEXT - Datacamp Limited, GB" and data["сеть"] == "CDNEXT-WAR"
    assert data["страна"] == "PL" and data["abuse"] == "abuse@datacamp.co.uk"
    assert [x.split(":")[0] for x in data["недоступно"]] == ["ipinfo", "internetdb"]
    assert "Tor" in data["недоступно"][0]


def test_ip_private_and_invalid(osint, server, capsys):
    res = infra.ip(osint.cfg(), osint.case(), "192.168.1.10")
    assert "Частный адрес" in res["note"]
    assert "shodan" not in res  # без ключа не викликається
    assert osint("infra", "ip", "not-an-ip") == 1


def test_ip_api_errors_do_not_leak_keys(osint, server, monkeypatch):
    """Секрети не повинні потрапляти у файли справи навіть через тексти помилок."""
    osint.configure(keys=KEYS)
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("dns.google", [], [ip]))
    for host, path in (("api.shodan.io", "/shodan/host/8.8.8.8"), ("api.abuseipdb.com", "/api/v2/check"),
                       ("api.criminalip.io", "/v1/asset/ip/report"),
                       ("www.virustotal.com", "/api/v3/ip_addresses/8.8.8.8")):
        server.add(server.ext(host, path), "denied", status=403)
    assert osint("infra", "ip", "8.8.8.8") == 0
    text = osint.case_text()
    assert "403" in text
    for secret in KEYS.values():
        assert secret not in text


def test_hash(osint, server):
    osint.configure(keys={"virustotal": KEYS["virustotal"]})
    h = "a" * 64
    server.add(server.ext("www.virustotal.com", f"/api/v3/files/{h}"),
               {"data": {"attributes": {"last_analysis_stats": {"malicious": 3}, "first_submission_date": 1700000000,
                                        "meaningful_name": "x.exe"}}})
    assert osint("infra", "hash", h.upper()) == 0
    [finding] = osint.findings()
    assert finding["data"] == {"malicious": 3}
    res = infra.file_hash(osint.cfg(), osint.case(), h)
    assert res["virustotal"]["first_submission_date"] == "2023-11-14T22:13:20+00:00"


def test_hash_not_found(osint, server):
    osint.configure(keys={"virustotal": KEYS["virustotal"]})
    res = infra.file_hash(osint.cfg(), osint.case(), "b" * 64)
    assert res["virustotal"] == {"note": "Нет в базе VirusTotal"}


def test_url_check(osint, server, monkeypatch):
    osint.configure(keys={"virustotal": KEYS["virustotal"], "urlscan": KEYS["urlscan"]})
    monkeypatch.setattr(infra.time, "sleep", lambda s: None)
    uid = "aHR0cHM6Ly9waGlzaC50ZXN0L2xvZ2lu"  # base64url("https://phish.test/login") без "="
    server.add(server.ext("www.virustotal.com", f"/api/v3/urls/{uid}"),
               {"data": {"attributes": {"last_final_url": "https://phish.test/login"}}})
    server.add(server.ext("urlscan.io", "/api/v1/scan/"), {"uuid": "u-1"}, method="POST")
    server.add(server.ext("urlscan.io", "/api/v1/result/u-1/"),
               {"page": {"domain": "phish.test", "ip": "1.2.3.4"}, "verdicts": {"overall": {"malicious": True}}})
    server.add(server.ext("urlscan.io", "/screenshots/u-1.png"), b"\x89PNG", ctype="image/png")

    assert osint("infra", "url", "https://phish.test/login") == 0
    [finding] = osint.findings()
    assert finding["data"]["virustotal"]["last_final_url"] == "https://phish.test/login"
    assert finding["data"]["urlscan"]["verdict"] == {"malicious": True}
    assert any(e["file"].endswith("urlscan_u-1.png") for e in osint.evidence())
    scan = server.hits(server.ext("urlscan.io", "/api/v1/scan/"))[0]
    assert b'"visibility": "unlisted"' in scan["body"]


def test_webcheck(osint, server):
    for check in infra.WEBCHECK_CHECKS:
        server.add(f"/webcheck/api/{check}", {"check": check})
    server.add("/webcheck/api/carbon", "Internal", ctype="text/plain", status=200)
    assert osint("infra", "webcheck", "example.test") == 0
    [finding] = osint.findings()
    assert finding["data"] == {"ok_checks": len(infra.WEBCHECK_CHECKS) - 1}
    assert server.hits("/webcheck/api/dns")[0]["query"]["url"] == ["https://example.test"]


def _self_signed(tmp_path):
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(0xABC).not_valid_before(now).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_file, key_file


def test_ssl_cert_self_signed_is_captured_but_not_verified(tmp_path):
    import ssl
    import threading

    cert_file, key_file = _self_signed(tmp_path)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve():
        for _ in range(2):  # знімок сертифіката + окрема перевірка
            conn, _ = listener.accept()
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                pass  # клієнт-перевірка рве рукостискання на самопідписаному сертифікаті

    threading.Thread(target=serve, daemon=True).start()
    res = infra.ssl_cert("localhost", port, timeout=5)
    listener.close()

    assert res["subject"] == "localhost" and res["serial"] == "ABC"
    assert res["san"] == ["localhost"] and 29 <= res["days_left"] <= 30
    assert res["verified"] is False
    assert "self" in res["verify_error"].lower()


def test_ip_answer_about_someone_else_is_dropped(osint, server, monkeypatch):
    """Ответ сервиса не про запрошенный IP (например, про того, кто спрашивает) в дело не попадает."""
    monkeypatch.setattr(infra, "_txt", fake_txt)
    monkeypatch.setattr(infra, "reverse_dns", lambda a: {"ptr": None})
    server.add(server.ext("ipinfo.io", "/8.8.8.8/json"), {"ip": "203.0.113.77", "city": "Мой город"})
    server.add(server.ext("internetdb.shodan.io", "/8.8.8.8"), {"ip": "203.0.113.77", "ports": [22]})
    infra.ip(osint.cfg(), osint.case(), "8.8.8.8")
    text = osint.case_text()
    assert "203.0.113.77" not in text and "Мой город" not in text
    assert "не про запрошенный IP" in " ".join(osint.findings()[0]["data"]["недоступно"])
