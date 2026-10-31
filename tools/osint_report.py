"""Понятные отчёты из ответов инструментов: одна строка для голоса и подробности для панели."""
import json

MAX_PORTS = 5
MAX_ITEMS = 8


def _plain(value, indent=0):
    """Вложенные данные как строки «ключ: значение». Пустые поля не показываем."""
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.extend(_plain(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {item}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.extend(_plain(item, indent))
            else:
                lines.append(f"{pad}- {item}")
        return lines
    return [f"{pad}{value}"]


def _section(title, rows):
    rows = [(k, v) for k, v in rows if v not in (None, "", [], {})]
    if not rows:
        return []
    lines = [title]
    for key, value in rows:
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        lines.append(f"  {key}: {value}")
    return lines + [""]


def _date(value):
    return str(value)[:10] if value else None


def _name_host(servers):
    """Кто держит DNS: последние две части имени сервера."""
    for server in servers or []:
        parts = str(server).lower().rstrip(".").split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
    return None


def domain(data):
    dns = data.get("dns") or {}
    whois = data.get("whois") or {}
    ssl = data.get("ssl") or {}
    http = data.get("http") or {}
    subs = data.get("subdomains")
    subs = subs.get("subdomains") or subs.get("items") if isinstance(subs, dict) else subs
    parts = [str(data.get("domain", "Домен"))]
    host = _name_host(dns.get("NS"))
    if host:
        parts.append(f"DNS у {host}")
    if whois.get("creation_date"):
        parts.append(f"зареєстрований у {str(whois['creation_date'])[:4]}")
    if ssl.get("days_left") is not None:
        parts.append(f"сертифікат ще {ssl['days_left']} днів" if ssl["days_left"] >= 0 else "сертифікат прострочений")
    summary = parts[0] + ": " + ", ".join(parts[1:]) + "." if len(parts) > 1 else f"{parts[0]}: даних мало."
    lines = _section("DNS", [(k, dns.get(k)) for k in ("A", "AAAA", "MX", "NS", "TXT", "DMARC")] + [("DNSSEC", "так" if dns.get("DNSSEC") else None)])
    lines += _section("Реєстрація", [("реєстратор", whois.get("registrar")), ("створено", _date(whois.get("creation_date"))), ("діє до", _date(whois.get("expiration_date")))])
    lines += _section("Сертифікат", [("видав", ssl.get("issuer")), ("діє до", _date(ssl.get("valid_to"))), ("перевірений", "так" if ssl.get("verified") else "ні" if ssl else None), ("TLS", ssl.get("tls_version"))])
    lines += _section("Сайт", [("сервер", http.get("server")), ("технологія", http.get("powered_by"))])
    if subs:
        lines += _section("Піддомени", [("знайдено", len(subs)), ("перші", list(subs)[:MAX_ITEMS])])
    return summary, "\n".join(lines).strip()


def ip(data):
    info = data.get("ipinfo") or {}
    asn = data.get("asn") or {}
    reg = data.get("registry") or {}
    idb = data.get("internetdb") or {}
    reverse = data.get("reverse_dns") or {}
    ports = idb.get("ports") or []
    vulns = idb.get("vulns") or []
    org = info.get("org") or asn.get("as_name")
    place = ", ".join(str(v) for v in (info.get("city"), info.get("country")) if v)
    parts = [p for p in (org, place) if p]
    if ports:
        shown = ", ".join(str(p) for p in ports[:MAX_PORTS])
        more = len(ports) - MAX_PORTS
        parts.append(f"порти {shown}" + (f" і ще {more}" if more > 0 else ""))
    if vulns:
        parts.append(f"відомих вразливостей {len(vulns)}")
    summary = f"{data.get('ip', 'IP')}: " + (", ".join(parts) if parts else "даних мало") + "."
    lines = _section("Мережа", [("власник", org), ("ASN", asn.get("asn")), ("діапазон", asn.get("prefix")), ("хост", reverse.get("ptr") or info.get("hostname")), ("скарги", reg.get("abuse"))])
    place_note = "кілька точок у світі (anycast), місто умовне" if info.get("anycast") else None
    lines += _section("Місце, приблизно", [("місто", info.get("city")), ("регіон", info.get("region")), ("країна", info.get("country")), ("примітка", place_note)])
    lines += _section("З бази InternetDB, може бути застарілим", [("порти", ports), ("вразливості", vulns), ("теги", idb.get("tags"))])
    return summary, "\n".join(lines).strip()


SECURITY_HEADERS = {
    "strict-transport-security": "HSTS",
    "content-security-policy": "CSP",
    "x-frame-options": "X-Frame-Options",
    "x-content-type-options": "X-Content-Type-Options",
}


def headers(data):
    hops = data.get("redirects") or []
    head = {str(k).lower(): v for k, v in (data.get("headers") or {}).items()}
    last = hops[-1] if hops else {}
    missing = [name for key, name in SECURITY_HEADERS.items() if key not in head]
    parts = [f"код {last.get('status', '?')}"]
    if head.get("server"):
        parts.append(f"сервер {head['server']}")
    if len(hops) > 1:
        parts.append(f"редиректів {len(hops) - 1}")
    parts.append(f"немає {len(missing)} із {len(SECURITY_HEADERS)} заголовків захисту" if missing else "заголовки захисту є")
    summary = f"{last.get('url', 'Сайт')}: " + ", ".join(parts) + "."
    lines = ["Шлях"] + [f"  {h.get('status')} {h.get('url')}" for h in hops] + [""]
    lines += _section("Заголовки захисту", [("немає", missing)])
    lines += ["Усі заголовки"] + [f"  {k}: {v}" for k, v in head.items()]
    return summary, "\n".join(lines).strip()


def wayback(data):
    snaps = data.get("snapshots") or []
    count = data.get("count") or len(snaps)
    first = snaps[0].get("date") if snaps else None
    last = data.get("last") or (snaps[-1].get("date") if snaps else None)
    target = data.get("url", "Сайт")
    if not last and not count:
        return f"{target}: у вебархіві нічого не знайшла.", "Знімків немає."
    summary = f"{target}: останній знімок {str(last)[:10]}" + (f", усього {count}" if count else "") + "."
    lines = _section("Вебархів", [("знімків", count or None), ("перший", first), ("останній", last), ("посилання", data.get("last_url"))])
    if data.get("list_error"):
        lines.append("Повний список зараз недоступний: архів обмежив частоту запитів. Спробуй пізніше.")
    return summary, "\n".join(lines).strip()


def links(data):
    counts = data.get("counts") or {}
    inner, outer = counts.get("internal", 0), counts.get("external", 0)
    summary = f"{data.get('title') or data.get('url', 'Сторінка')}: {inner} внутрішніх і {outer} зовнішніх посилань."
    lines = _section("Сторінка", [("адреса", data.get("url")), ("назва", data.get("title")), ("внутрішніх", inner), ("зовнішніх", outer)])
    lines += _section("Контакти на сторінці", [("пошта", data.get("emails")), ("телефони", data.get("phones")), ("соцмережі", data.get("social"))])
    if data.get("csv"):
        lines.append(f"Повний список у справі yuna: {data['csv']}")
    return summary, "\n".join(lines).strip()


def tables(data):
    found = data.get("tables") or []
    summary = f"Знайшла таблиць: {len(found)}." if found else "Таблиць на сторінці немає."
    lines = []
    for table in found[:MAX_ITEMS]:
        head = ", ".join(str(h) for h in (table.get("header") or []))
        lines.append(f"Таблиця {table.get('table')}: рядків {table.get('rows')}, стовпців {table.get('cols')}. {head}".strip())
        if table.get("csv"):
            lines.append(f"  у справі yuna: {table['csv']}")
    return summary, "\n".join(lines)


def _verdict(stats):
    """Сколько антивирусов считает объект опасным."""
    if not isinstance(stats, dict):
        return None
    bad = int(stats.get("malicious", 0)) + int(stats.get("suspicious", 0))
    total = sum(v for v in stats.values() if isinstance(v, int))
    return bad, total


def reputation(data):
    vt = data.get("virustotal") if isinstance(data.get("virustotal"), dict) else {}
    target = data.get("url") or data.get("hash") or "Об'єкт"
    verdict = _verdict(vt.get("last_analysis_stats"))
    if verdict is None:
        summary = f"{target}: VirusTotal не дав оцінки, подробиці в панелі."
    elif verdict[0]:
        summary = f"{target}: небезпечним вважають {verdict[0]} із {verdict[1]} антивірусів."
    else:
        summary = f"{target}: загроз не знайдено, перевірили {verdict[1]} антивірусів."
    return summary, "\n".join(_plain(data))


def dorks(data):
    queries = data.get("queries") or []
    summary = f"Підготувала пошукових запитів: {len(queries)}, посилання в панелі."
    lines = []
    for item in queries:
        lines.append(item.get("query", ""))
        link = (item.get("links") or {}).get("Google")
        if link:
            lines.append(f"  {link}")
    return summary, "\n".join(lines)


REPORTS = {"domain": domain, "ip": ip, "headers": headers, "wayback": wayback, "links": links, "tables": tables,
           "urlcheck": reputation, "hash": reputation, "dorks": dorks}


def render(tool, data):
    """(строка для голоса, подробности). Незнакомый ответ показываем как есть, но не падаем."""
    if not isinstance(data, dict):
        return "Готово, результат у панелі.", str(data)
    try:
        return REPORTS[tool](data)
    except (AttributeError, KeyError, TypeError, ValueError):
        return "Готово, результат у панелі.", json.dumps(data, ensure_ascii=False, indent=1)


def web_text(tool, data):
    """Поиск, YouTube, карта и страница: понятный текст вместо JSON."""
    if not isinstance(data, dict):
        return str(data)
    if tool == "maps":
        return "\n".join(str(v) for v in (data.get("location"), data.get("url")) if v)
    lines = []
    for item in data.get("items") or []:
        lines.append(str(item.get("title") or ""))
        if item.get("author"):
            lines.append(f"  {item['author']}")
        if item.get("url"):
            lines.append(f"  {item['url']}")
        if item.get("text"):
            lines.append(f"  {item['text']}")
    return "\n".join(lines) if lines else json.dumps(data, ensure_ascii=False, indent=1)
