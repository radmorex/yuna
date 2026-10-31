"""Модуль infra: домени, IP, хеші, URL. Лише пасивні перевірки (без сканування цілі)."""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..case import Case, slug
from ..config import Config
from .. import net
from ..net import normalize_url, session, short_error

SECURITY_HEADERS = (
    "strict-transport-security", "content-security-policy", "x-frame-options",
    "x-content-type-options", "referrer-policy", "permissions-policy",
)


def _host(target: str) -> str:
    target = target.strip()
    if "://" in target:
        return urlparse(target).hostname or target
    return target.split("/")[0].split(":")[0]


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        msg = short_error(exc)
        if "403" in msg and "Forbidden" in msg:  # ipinfo, InternetDB и др. так отвечают на выходы Tor/VPN
            msg = "сервис отказал в доступе (403) — обычно так блокируют Tor и VPN"
        return {"error": msg}


def _ok(x) -> dict:
    return x if isinstance(x, dict) and "error" not in x else {}


def _failed(result: dict) -> list[str]:
    return [f"{k}: {v['error']}" for k, v in result.items() if isinstance(v, dict) and "error" in v]


CDN = {"cloudflare": "Cloudflare", "akamai": "Akamai", "fastly": "Fastly", "incapsula": "Imperva", "imperva": "Imperva",
       "sucuri": "Sucuri", "stackpath": "StackPath", "ddos-guard": "DDoS-Guard", "qrator": "Qrator", "cdn77": "CDN77",
       "bunny": "BunnyCDN", "edgecast": "Edgecast", "cloudfront": "CloudFront", "gcore": "Gcore"}
SUBS_SHOWN = 20


def cdn_name(owner: str | None) -> str | None:
    """Сеть доставки (CDN) перед сайтом: её адреса — не настоящий сервер, хостинг по ним не определить."""
    low = (owner or "").lower()
    return next((name for key, name in CDN.items() if key in low), None)


def _day(value) -> str | None:
    """Дата WHOIS «2016-02-18 21:07:42+00:00» → «18.02.2016»: часы и пояс для даты регистрации — шум."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{m[3]}.{m[2]}.{m[1]}" if m else (str(value) if value else None)


def _compact(d: dict) -> dict:
    """Сводка без пустых полей: в записи только то, что реально известно."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ---------------- DNS / WHOIS / SSL ----------------
DOH = "https://cloudflare-dns.com/dns-query"
DNS_TYPES = {"A": 1, "AAAA": 28, "CNAME": 5, "MX": 15, "NS": 2, "TXT": 16, "SOA": 6, "CAA": 257, "PTR": 12, "DNSKEY": 48}


def _doh(name: str, rtype: str, timeout: int) -> list[str]:
    """DNS через HTTPS (в режиме Tor): запрос идёт через Tor как обычный HTTPS — провайдер не видит,
    какие домены вы проверяете, а UDP-запросы мимо Tor не нужны."""
    from ..config import DEFAULT_UA

    r = session(DEFAULT_UA, retries=1).get(DOH, params={"name": name, "type": rtype},
                                           headers={"Accept": "application/dns-json"}, timeout=timeout)
    r.raise_for_status()
    out = []
    for a in r.json().get("Answer", []):
        if a.get("type") != DNS_TYPES[rtype]:
            continue
        data = str(a.get("data", ""))
        if rtype == "TXT":  # "часть1" "часть2" → одной строкой, как у dnspython
            parts = re.findall(r'"((?:[^"\\]|\\.)*)"', data)
            data = "".join(parts) if parts else data
        out.append(data)
    return out


def _resolver(timeout: int):
    import dns.resolver

    res = dns.resolver.Resolver()
    res.lifetime = timeout
    return res


def dns_records(domain: str, timeout: int = 10) -> dict:
    if net.routed():
        return _dns_records_doh(domain, timeout)
    import dns.resolver

    res = _resolver(timeout)
    out: dict = {}
    for rtype in ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "CAA"):
        try:
            answers = res.resolve(domain, rtype)
            out[rtype] = sorted(a.to_text().strip('"') if rtype != "TXT" else
                                "".join(s.decode(errors="replace") for s in a.strings) for a in answers)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            continue
        except dns.exception.Timeout:
            out.setdefault("errors", []).append(f"{rtype}: timeout")
    try:
        dmarc = res.resolve(f"_dmarc.{domain}", "TXT")
        out["DMARC"] = ["".join(s.decode(errors="replace") for s in a.strings) for a in dmarc]
    except Exception:  # noqa: BLE001
        pass
    out["SPF"] = [t for t in out.get("TXT", []) if t.lower().startswith("v=spf1")]
    try:
        res.resolve(domain, "DNSKEY")
        out["DNSSEC"] = True
    except Exception:  # noqa: BLE001
        out["DNSSEC"] = False
    return out


def _dns_records_doh(domain: str, timeout: int) -> dict:
    out: dict = {}
    for rtype in ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "CAA"):
        try:
            found = _doh(domain, rtype, timeout)
            if found:
                out[rtype] = sorted(found)
        except Exception as exc:  # noqa: BLE001
            out.setdefault("errors", []).append(f"{rtype}: {short_error(exc)}")
    try:
        out["DMARC"] = _doh(f"_dmarc.{domain}", "TXT", timeout)
    except Exception:  # noqa: BLE001
        pass
    out["SPF"] = [t for t in out.get("TXT", []) if t.lower().startswith("v=spf1")]
    try:
        out["DNSSEC"] = bool(_doh(domain, "DNSKEY", timeout))
    except Exception:  # noqa: BLE001
        out["DNSSEC"] = False
    out["resolver"] = "DNS-over-HTTPS через Tor"
    return out


def whois_info(domain: str) -> dict:
    import whois

    w = whois.whois(domain)
    keep = ("domain_name", "registrar", "creation_date", "updated_date", "expiration_date",
            "name_servers", "status", "emails", "org", "country", "registrant_name", "dnssec")
    data = {}
    for k in keep:
        v = w.get(k) if hasattr(w, "get") else getattr(w, k, None)
        if v in (None, "", []):
            continue
        if isinstance(v, (list, tuple, set)):
            v = sorted({str(i) for i in v})
        data[k] = v
    return data


def _verify_tls(host: str, port: int, timeout: int) -> tuple[bool, str | None]:
    """Чи проходить сертифікат звичайну перевірку (ланцюжок довіри + ім'я хоста)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
                return True, None
    except ssl.SSLCertVerificationError as exc:
        return False, exc.verify_message or short_error(exc)
    except (ssl.SSLError, OSError) as exc:
        return False, short_error(exc)


def ssl_cert(host: str, port: int = 443, timeout: int = 10) -> dict:
    """Сертифікат як його віддає сервер — і самопідписаний чи прострочений теж
    (типово для фішингу), тому перевірка вимкнена; її результат пишемо окремо в verified."""
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
            version = tls.version()
    cert = x509.load_der_x509_certificate(der)

    def name(n, oid):
        vals = n.get_attributes_for_oid(oid)
        return vals[0].value if vals else None

    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        sans = san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    not_after = cert.not_valid_after_utc
    import hashlib

    verified, verify_error = _verify_tls(host, port, timeout)
    return {
        "verified": verified,
        "verify_error": verify_error,
        "subject": name(cert.subject, NameOID.COMMON_NAME),
        "issuer": name(cert.issuer, NameOID.ORGANIZATION_NAME) or name(cert.issuer, NameOID.COMMON_NAME),
        "valid_from": cert.not_valid_before_utc.isoformat(),
        "valid_to": not_after.isoformat(),
        "days_left": (not_after - datetime.now(timezone.utc)).days,
        "serial": format(cert.serial_number, "X"),
        "sha256": hashlib.sha256(der).hexdigest(),
        "san": sans[:200],
        "san_count": len(sans),
        "tls_version": version,
    }


def http_info(cfg: Config, domain: str) -> dict:
    s = session(cfg.user_agent, retries=1)
    r = s.get(f"https://{domain}", timeout=cfg.timeout, allow_redirects=True, stream=True)
    r.close()
    chain = [{"status": h.status_code, "url": h.url} for h in r.history] + [{"status": r.status_code, "url": r.url}]
    headers = {k.lower(): v for k, v in r.headers.items()}
    return {
        "redirects": chain,
        "server": headers.get("server"),
        "powered_by": headers.get("x-powered-by"),
        "headers": headers,
        "security_headers_missing": [h for h in SECURITY_HEADERS if h not in headers],
        "cookies_set": [c.name for c in r.cookies],
    }


def robots(cfg: Config, domain: str) -> dict:
    s = session(cfg.user_agent, retries=1)
    out = {}
    for path in ("/robots.txt", "/.well-known/security.txt", "/sitemap.xml"):
        try:
            r = s.get(f"https://{domain}{path}", timeout=cfg.timeout)
            if r.status_code == 200 and len(r.text) < 500_000 and "<html" not in r.text[:500].lower():
                out[path] = r.text[:20000]
        except Exception:  # noqa: BLE001
            continue
    return out


def crtsh_subdomains(cfg: Config, domain: str) -> dict:
    """Піддомени з журналів прозорості сертифікатів (crt.sh)."""
    s = session(cfg.user_agent, retries=2)
    r = s.get("https://crt.sh/", params={"q": f"%.{domain}", "output": "json"}, timeout=cfg.timeout * 4)
    r.raise_for_status()
    names = set()
    for row in r.json():
        for n in str(row.get("name_value", "")).splitlines():
            n = n.strip().lower().lstrip("*.")
            if n.endswith(domain):
                names.add(n)
    return {"count": len(names), "subdomains": sorted(names)}


# ---------------- реєстри: чия мережа (без ключів, працюють і через Tor) ----------------
RIPESTAT = "https://stat.ripe.net/data"


def _txt(name: str, timeout: int = 10) -> list[str]:
    if net.routed():
        return _doh(name, "TXT", timeout)
    import dns.resolver

    try:
        ans = _resolver(timeout).resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    return ["".join(p.decode() for p in r.strings) for r in ans]


def asn_info(ip: str, timeout: int = 10) -> dict:
    """Team Cymru по DNS: автономная система (провайдер), префикс, страна по реестру."""
    a = ipaddress.ip_address(ip)
    if a.version == 4:
        q = ".".join(reversed(str(a).split("."))) + ".origin.asn.cymru.com"
    else:
        q = ".".join(reversed(a.exploded.replace(":", ""))) + ".origin6.asn.cymru.com"
    rows = _txt(q, timeout)
    if not rows:
        return {"note": "нет данных"}
    asn, prefix, cc, registry, allocated = [x.strip() for x in rows[0].split("|")][:5]
    asn = asn.split()[0]
    named = _txt(f"AS{asn}.asn.cymru.com", timeout)
    return {"asn": f"AS{asn}", "as_name": named[0].split("|")[-1].strip() if named else None,
            "prefix": prefix, "country": cc, "registry": registry, "allocated": allocated}


def registry_whois(cfg: Config, ip: str) -> dict:
    """RIPEstat: запись сети из регионального реестра (RIPE, ARIN, APNIC… — сервис сам спрашивает нужный)
    и abuse-контакт сети."""
    s = session(cfg.user_agent, retries=1)
    r = s.get(f"{RIPESTAT}/whois/data.json", params={"resource": ip}, timeout=cfg.timeout * 2)
    r.raise_for_status()
    records = r.json().get("data", {}).get("records") or [[]]
    rec: dict = {}
    for row in records[0]:
        rec.setdefault(str(row.get("key", "")).lower(), row.get("value"))
    out = {k: rec[k] for k in ("netname", "inetnum", "inet6num", "netrange", "cidr", "country", "org", "orgname",
                               "descr", "created", "last-modified", "regdate", "updated") if rec.get(k)}
    a = s.get(f"{RIPESTAT}/abuse-contact-finder/data.json", params={"resource": ip}, timeout=cfg.timeout * 2)
    if a.ok:
        out["abuse"] = a.json().get("data", {}).get("abuse_contacts") or []
    return out


# ---------------- безкоштовні джерела по IP ----------------
def _about(ip: str, data: dict, via: str) -> dict:
    """Ответ должен быть про запрошенный IP. Справочный сервис без адреса отдаёт данные о том, КТО спрашивает,
    — при повторе напрямую это был бы IP искателя, такой ответ в дело не попадает."""
    if ipaddress.ip_address(str(data.get("ip", "")).strip() or "0.0.0.0") != ipaddress.ip_address(ip):
        raise RuntimeError("ответ сервиса не про запрошенный IP — отброшен")
    return data | ({"_маршрут": "напрямую (сервис блокирует Tor)"} if via == "напрямую" and net.routed() else {})


def internetdb(cfg: Config, ip: str) -> dict:
    """Shodan InternetDB: порти, CVE, теги — без ключа."""
    ip = str(ipaddress.ip_address(ip))  # без адреса сервис ответил бы про того, кто спрашивает
    r, via = net.lookup_get(cfg, session(cfg.user_agent, retries=1), f"https://internetdb.shodan.io/{ip}", timeout=cfg.timeout)
    if r.status_code == 404:
        return {"note": "IP нет в базе InternetDB"}
    r.raise_for_status()
    return _about(ip, r.json(), via)


def ipinfo(cfg: Config, ip: str) -> dict:
    ip = str(ipaddress.ip_address(ip))
    r, via = net.lookup_get(cfg, session(cfg.user_agent, retries=1), f"https://ipinfo.io/{ip}/json", timeout=cfg.timeout)
    r.raise_for_status()
    data = r.json()
    data.pop("readme", None)
    return _about(ip, data, via)


def reverse_dns(ip: str) -> dict:
    if net.routed():  # системный резолвер раскрыл бы провайдеру, какой IP вы проверяете
        import ipaddress

        try:
            ptr = _doh(ipaddress.ip_address(ip).reverse_pointer, "PTR", 10)
            return {"ptr": ptr[0].rstrip(".") if ptr else None, "aliases": []}
        except Exception:  # noqa: BLE001
            return {"ptr": None}
    try:
        host, aliases, _ = socket.gethostbyaddr(ip)
        return {"ptr": host, "aliases": aliases}
    except (socket.herror, socket.gaierror):
        return {"ptr": None}


# ---------------- джерела з ключами ----------------
def shodan_host(cfg: Config, ip: str) -> dict:
    s = session(cfg.user_agent, retries=1)
    r = s.get(f"https://api.shodan.io/shodan/host/{ip}", params={"key": cfg.key("shodan")}, timeout=cfg.timeout * 2)
    if r.status_code == 404:
        return {"note": "IP нет в Shodan"}
    r.raise_for_status()
    d = r.json()
    services = [{"port": x.get("port"), "transport": x.get("transport"), "product": x.get("product"),
                 "version": x.get("version"), "banner": (x.get("data") or "")[:300]} for x in d.get("data", [])]
    return {k: d.get(k) for k in ("org", "isp", "asn", "os", "country_name", "city", "hostnames",
                                  "domains", "ports", "vulns", "last_update")} | {"services": services}


def _vt(cfg: Config, path: str) -> dict:
    s = session(cfg.user_agent, retries=1)
    r = s.get(f"https://www.virustotal.com/api/v3/{path}", headers={"x-apikey": cfg.key("virustotal")},
              timeout=cfg.timeout * 2)
    if r.status_code == 404:
        return {"note": "Нет в базе VirusTotal"}
    r.raise_for_status()
    attr = r.json().get("data", {}).get("attributes", {})
    keep = ("last_analysis_stats", "reputation", "categories", "tags", "country", "as_owner", "asn",
            "network", "registrar", "creation_date", "meaningful_name", "names", "type_description",
            "size", "first_submission_date", "last_analysis_date", "title", "last_final_url")
    out = {k: attr[k] for k in keep if k in attr}
    for k in ("creation_date", "first_submission_date", "last_analysis_date"):
        if isinstance(out.get(k), int):
            out[k] = datetime.fromtimestamp(out[k], timezone.utc).isoformat()
    bad = {n: v.get("result") for n, v in (attr.get("last_analysis_results") or {}).items()
           if v.get("category") in ("malicious", "suspicious")}
    if bad:
        out["flagged_by"] = bad
    return out


def abuseipdb(cfg: Config, ip: str) -> dict:
    s = session(cfg.user_agent, retries=1)
    r = s.get("https://api.abuseipdb.com/api/v2/check", params={"ipAddress": ip, "maxAgeInDays": 90},
              headers={"Key": cfg.key("abuseipdb"), "Accept": "application/json"}, timeout=cfg.timeout)
    r.raise_for_status()
    return r.json().get("data", {})


def criminalip(cfg: Config, ip: str) -> dict:
    s = session(cfg.user_agent, retries=1)
    r = s.get("https://api.criminalip.io/v1/asset/ip/report", params={"ip": ip},
              headers={"x-api-key": cfg.key("criminalip")}, timeout=cfg.timeout * 2)
    r.raise_for_status()
    d = r.json()
    ports = [{"port": p.get("open_port_no"), "service": p.get("app_name"), "version": p.get("app_version"),
              "protocol": p.get("protocol")} for p in (d.get("port") or {}).get("data", [])]
    vulns = [v.get("cve_id") for v in (d.get("vulnerability") or {}).get("data", []) if v.get("cve_id")]
    return {"score": d.get("score"), "issues": d.get("issues"), "ports": ports, "cves": sorted(set(vulns))}


# ---------------- публічні функції ----------------
def domain(cfg: Config, case: Case, target: str, subdomains: bool = True) -> dict:
    d = _host(target).lower().rstrip(".")
    result: dict = {"domain": d}
    result["dns"] = _safe(dns_records, d, cfg.timeout)
    result["whois"] = _safe(whois_info, d)
    result["ssl"] = _safe(ssl_cert, d, 443, cfg.timeout)
    result["http"] = _safe(http_info, cfg, d)
    result["files"] = _safe(robots, cfg, d)
    if subdomains:
        result["subdomains"] = _safe(crtsh_subdomains, cfg, d)
    if cfg.key("virustotal"):
        result["virustotal"] = _safe(_vt, cfg, f"domains/{d}")
    ips = _ok(result["dns"]).get("A") or []
    if ips:
        result["hosting_asn"] = _safe(asn_info, ips[0], cfg.timeout)
        result["ip_hosting"] = {ip: _safe(ipinfo, cfg, ip) for ip in ips[:3]}
    out = case.save_json("infra", f"domain_{slug(d)}.json", result, d, "DNS/WHOIS/SSL/HTTP")
    dns, whois, cert, http = (_ok(result[k]) for k in ("dns", "whois", "ssl", "http"))
    asn = _ok(result.get("hosting_asn"))
    hosting = " ".join(x for x in (asn.get("asn"), asn.get("as_name")) if x) or next(
        (h.get("org") for h in (result.get("ip_hosting") or {}).values() if _ok(h).get("org")), None)
    failed = _failed(result) + ([] if hosting else _failed(result.get("ip_hosting") or {}))  # хостинг известен — сбои ipinfo не важны
    subs = [n for n in _ok(result.get("subdomains")).get("subdomains") or [] if n != d]
    cdn = cdn_name(hosting) or cdn_name(http.get("server"))
    case.add_finding("infra", d, f"Домен {d}", _compact({
        "IP-адреса": dns.get("A"), "хостинг": hosting,
        "CDN": f"{cdn} — адреса принадлежат сети доставки, настоящий сервер сайта скрыт" if cdn else None,
        "регистратор": whois.get("registrar"), "создан": _day(whois.get("creation_date")),
        "истекает": _day(whois.get("expiration_date")), "NS": [n.rstrip(".") for n in dns.get("NS") or []],
        "SSL": f"{cert['issuer']}, осталось {cert['days_left']} дн." if cert.get("issuer") and cert.get("days_left") is not None
               else cert.get("issuer"),
        "сервер": http.get("server"),
        "поддомены": subs[:SUBS_SHOWN] + ([f"… ещё {len(subs) - SUBS_SHOWN}"] if len(subs) > SUBS_SHOWN else []),
        "недоступно": failed,
    }), [out])
    return result


def ip(cfg: Config, case: Case, target: str) -> dict:
    addr = str(ipaddress.ip_address(target.strip()))
    result: dict = {"ip": addr}
    if ipaddress.ip_address(addr).is_private:
        result["note"] = "Частный адрес (локальная сеть), внешние базы ничего не найдут"
    result["reverse_dns"] = _safe(reverse_dns, addr)
    result["asn"] = _safe(asn_info, addr, cfg.timeout)
    result["registry"] = _safe(registry_whois, cfg, addr)
    result["ipinfo"] = _safe(ipinfo, cfg, addr)
    result["internetdb"] = _safe(internetdb, cfg, addr)
    for key, fn in (("shodan", shodan_host), ("abuseipdb", abuseipdb), ("criminalip", criminalip)):
        if cfg.key(key):
            result[key] = _safe(fn, cfg, addr)
    if cfg.key("virustotal"):
        result["virustotal"] = _safe(_vt, cfg, f"ip_addresses/{addr}")
    out = case.save_json("infra", f"ip_{addr.replace(':', '_')}.json", result, addr, "данные по IP")
    asn, reg, info, idb = (_ok(result[k]) for k in ("asn", "registry", "ipinfo", "internetdb"))
    case.add_finding("infra", addr, f"IP {addr}", _compact({
        "провайдер": (owner := " ".join(x for x in (asn.get("asn"), asn.get("as_name")) if x) or info.get("org")),
        "CDN": f"{cdn_name(owner)} — адрес сети доставки, за ним может стоять любой сайт" if cdn_name(owner) else None,
        "сеть": reg.get("netname"),
        "владелец": reg.get("orgname") or reg.get("org") or (reg.get("descr") if not reg.get("netname") else None),
        "диапазон": reg.get("inetnum") or reg.get("inet6num") or reg.get("netrange") or reg.get("cidr") or asn.get("prefix"),
        "страна": reg.get("country") or asn.get("country") or info.get("country"),
        "город": info.get("city"),
        "ptr": _ok(result["reverse_dns"]).get("ptr"),
        "abuse": ", ".join(reg.get("abuse") or []),
        "порты": idb.get("ports"), "уязвимости": idb.get("vulns"),
        "недоступно": _failed(result),
    }), [out])
    return result


def file_hash(cfg: Config, case: Case, h: str) -> dict:
    if not cfg.key("virustotal"):
        raise RuntimeError("Нужен ключ VirusTotal (keys.virustotal в config.toml)")
    h = h.strip().lower()
    result = {"hash": h, "virustotal": _vt(cfg, f"files/{h}")}
    out = case.save_json("infra", f"hash_{h[:16]}.json", result, "virustotal.com", "отчёт по хешу")
    case.add_finding("infra", h, "Хеш файла (VirusTotal)", result["virustotal"].get("last_analysis_stats", result), [out])
    return result


def url_check(cfg: Config, case: Case, url: str, wait: int = 60) -> dict:
    url = normalize_url(url)
    result: dict = {"url": url}
    if not cfg.key("virustotal") and not cfg.key("urlscan"):
        raise RuntimeError("Нужен ключ VirusTotal или urlscan.io в config.toml")
    if cfg.key("virustotal"):
        uid = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        result["virustotal"] = _safe(_vt, cfg, f"urls/{uid}")
    if cfg.key("urlscan"):
        result["urlscan"] = _safe(_urlscan, cfg, case, url, wait)
    out = case.save_json("infra", f"url_{slug(url, 40)}.json", result, url, "проверка URL")
    case.add_finding("infra", url, "Проверка URL", result, [out])
    return result


def _urlscan(cfg: Config, case: Case, url: str, wait: int) -> dict:
    s = session(cfg.user_agent, retries=1)
    headers = {"API-Key": cfg.key("urlscan"), "Content-Type": "application/json"}
    r = s.post("https://urlscan.io/api/v1/scan/", json={"url": url, "visibility": "unlisted"},
               headers=headers, timeout=cfg.timeout)
    r.raise_for_status()
    uuid = r.json()["uuid"]
    deadline = time.time() + wait
    time.sleep(10)
    while time.time() < deadline:
        res = s.get(f"https://urlscan.io/api/v1/result/{uuid}/", timeout=cfg.timeout)
        if res.status_code == 200:
            d = res.json()
            page = d.get("page", {})
            verdict = d.get("verdicts", {}).get("overall", {})
            shot = s.get(f"https://urlscan.io/screenshots/{uuid}.png", timeout=cfg.timeout)
            if shot.status_code == 200:
                case.save_bytes("infra", f"urlscan_{uuid}.png", shot.content, "urlscan.io", "скриншот urlscan")
            return {"uuid": uuid, "report": f"https://urlscan.io/result/{uuid}/",
                    "page": {k: page.get(k) for k in ("url", "domain", "ip", "country", "server", "title", "asnname")},
                    "verdict": verdict}
        time.sleep(5)
    return {"uuid": uuid, "report": f"https://urlscan.io/result/{uuid}/", "note": "Скан ещё идёт, откройте отчёт позже"}


WEBCHECK_CHECKS = (
    "get-ip", "status", "ssl", "tls", "dns", "dnssec", "headers", "http-security", "hsts", "cookies",
    "redirects", "robots-txt", "security-txt", "sitemap", "txt-records", "mail-config", "whois",
    "ports", "trace-route", "firewall", "tech-stack", "social-tags", "linked-pages", "carbon", "rank",
)


def webcheck(cfg: Config, case: Case, target: str) -> dict:
    """Виклик локально розгорнутого Web-Check (github.com/lissy93/web-check)."""
    if not cfg.webcheck_url:
        raise RuntimeError("Не указан services.webcheck_url в config.toml (например http://localhost:3000)")
    url = normalize_url(target)
    s = session(cfg.user_agent, retries=0)
    result: dict = {"url": url, "checks": {}}
    for check in WEBCHECK_CHECKS:
        try:
            r = s.get(f"{cfg.webcheck_url}/api/{check}", params={"url": url}, timeout=cfg.timeout * 3)
            ctype = r.headers.get("content-type", "")
            result["checks"][check] = r.json() if "json" in ctype else {"status": r.status_code, "body": r.text[:500]}
        except Exception as exc:  # noqa: BLE001
            result["checks"][check] = {"error": short_error(exc)}
    ok = sum(1 for v in result["checks"].values() if not (isinstance(v, dict) and ("error" in v or "status" in v)))
    result["ok_checks"] = ok
    out = case.save_json("infra", f"webcheck_{slug(url, 40)}.json", result, cfg.webcheck_url, "Web-Check")
    case.add_finding("infra", url, f"Web-Check: {ok}/{len(WEBCHECK_CHECKS)} проверок", {"ok_checks": ok}, [out])
    return result
