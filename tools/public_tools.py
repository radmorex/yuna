"""Bounded, read-only public web tools. No cookies, credentials, shell or private hosts."""
import http.client
import gzip
import io
import ipaddress
import json
import re
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

class ToolError(Exception):
    pass

def public_target(url):
    if not isinstance(url, str) or len(url) > 2000 or re.search(r'[\x00-\x20\x7f\\]', url):
        raise ToolError('Нужна обычная публичная ссылка http или https.')
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').encode('idna').decode().lower().rstrip('.')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except (ValueError, UnicodeError):
        raise ToolError('Не удалось прочитать ссылку.') from None
    if parts.scheme not in ('http', 'https') or parts.username is not None or parts.password is not None or not host or port not in (80,443):
        raise ToolError('Разрешены только публичные сайты http/https без пароля и нестандартного порта.')
    if host == 'localhost' or host.endswith(('.localhost','.local','.internal')):
        raise ToolError('Локальные и служебные адреса недоступны.')
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses):
            raise ToolError('Локальные и служебные адреса недоступны.')
    except (OSError, ValueError):
        raise ToolError('Не удалось определить публичный адрес сайта.') from None
    # Validate every answer, then pin the connection to one validated IP; TLS uses the original host.
    addresses.sort(key=lambda ip: ':' in ip)
    return parts, host, port, addresses[0]

def fetch(url, limit=1_500_000, redirects=3):
    for _ in range(redirects+1):
        parts, host, port, address = public_target(url)
        connection = http.client.HTTPConnection(host, port, timeout=9)
        try:
            sock = socket.create_connection((address, port), timeout=9)
            connection.sock = sock
            if parts.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection.sock = sock
            path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
            # Non-ASCII paths are encoded without changing existing percent escapes.
            path = quote(path, safe="/%?=&:+,;@!$'()*~-._")
            connection.request('GET', path, headers={'Host':host, 'User-Agent':'Mozilla/5.0 (compatible; Yuna)', 'Accept-Encoding':'identity', 'Accept':'text/html,application/json,application/xml,text/plain;q=0.9,*/*;q=0.1'})
            response = connection.getresponse()
            if response.status in (301,302,303,307,308):
                location = response.getheader('Location')
                if not location: raise ToolError('Сайт вернул пустое перенаправление.')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ToolError(f'Источник недоступен (HTTP {response.status}).')
            encoding=response.getheader('Content-Encoding','identity').lower()
            raw = response.read(limit+1)
            if len(raw)>limit: raise ToolError('Страница слишком большая для быстрого чтения.')
            if encoding=='gzip':
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as archive: raw=archive.read(limit+1)
                except (OSError,EOFError): raise ToolError('Источник вернул повреждённую страницу.') from None
                if len(raw)>limit: raise ToolError('Распакованная страница слишком большая.')
            elif encoding not in ('identity',''):
                raise ToolError('Источник вернул неподдерживаемое сжатие.')
            mime = response.getheader('Content-Type','').lower()
            if not any(kind in mime for kind in ('text/','json','xml','rss')):
                raise ToolError('По ссылке нужен текст или обычная веб-страница.')
            return raw, url, mime
        except ToolError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError):
            raise ToolError('Источник не ответил. Попробуй другую ссылку.') from None
        finally:
            connection.close()
    raise ToolError('Слишком много перенаправлений.')

def get_json(url):
    raw, final, mime = fetch(url, limit=750_000)
    try: return json.loads(raw)
    except ValueError: raise ToolError('Источник вернул неверный ответ.') from None

class PageText(HTMLParser):
    ignore={'script','style','noscript','svg','nav','footer','form','template'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.depth=0; self.title=False; self.titles=[]; self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag in self.ignore: self.depth+=1
        if tag=='title': self.title=True
    def handle_endtag(self, tag):
        if tag in self.ignore and self.depth: self.depth-=1
        if tag=='title': self.title=False
    def handle_data(self, text):
        if self.title: self.titles.append(text)
        if not self.depth and text.strip(): self.parts.append(text.strip())

def read_page(url):
    raw, final, mime = fetch(url)
    text = raw.decode('utf-8', errors='replace')
    if 'html' in mime:
        parser=PageText(); parser.feed(text)
        title=' '.join(parser.titles).strip()[:180]
        text=' '.join(parser.parts)
    else: title=urlsplit(final).hostname
    text=re.sub(r'\s+',' ',text).strip()
    if len(text)<40: raise ToolError('На странице мало доступного текста; возможно, она требует JavaScript или вход.')
    return {'title':title or urlsplit(final).hostname,'url':final,'text':text[:6000], 'kind':'page'}

def search_web(query):
    url='https://www.bing.com/search?'+urlencode({'q':query,'format':'rss'})
    raw, _, _=fetch(url,limit=600_000)
    try:
        root=ET.fromstring(raw)
        results=[]
        for item in root.findall('./channel/item'):
            link=item.findtext('link','')
            if urlsplit(link).scheme not in ('http','https'): continue
            results.append({'title':unescape(item.findtext('title',''))[:180], 'url':link, 'text':unescape(item.findtext('description',''))[:900], 'kind':'search'})
            if len(results)==4: break
    except ET.ParseError: raise ToolError('Поиск временно недоступен.') from None
    if not results: raise ToolError('Поиск не вернул результатов. Попробуй изменить запрос.')
    return {'kind':'web','title':query,'items':results,'source':'Bing RSS','url':url,'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}

def geocode(place):
    place=re.sub(r'\s+(?:сегодня|завтра|зараз|сейчас|сьогодні).*$', '',place,flags=re.I).strip(' ?.,')
    aliases={'киев':'Kyiv','київ':'Kyiv','киеве':'Kyiv','києві':'Kyiv','львов':'Lviv','львів':'Lviv','львове':'Lviv','львові':'Lviv','одесса':'Odesa','одеса':'Odesa','одессе':'Odesa','одесі':'Odesa','лондон':'London','лондоне':'London','лондоні':'London','париж':'Paris','париже':'Paris','парижі':'Paris','берлин':'Berlin','берлін':'Berlin','берлине':'Berlin','берліні':'Berlin','варшава':'Warsaw','варшаве':'Warsaw','варшаві':'Warsaw','харків':'Kharkiv','харьков':'Kharkiv','харкові':'Kharkiv','харькове':'Kharkiv','дніпро':'Dnipro','днепр':'Dnipro','дніпрі':'Dnipro','запоріжжя':'Zaporizhzhia','житомир':'Zhytomyr','тернопіль':'Ternopil','івано-франківськ':'Ivano-Frankivsk','ужгород':'Uzhhorod','вінниця':'Vinnytsia','чернігів':'Chernihiv','суми':'Sumy','чернівці':'Chernivtsi','черкаси':'Cherkasy','полтава':'Poltava','хмельницький':'Khmelnytskyi','рівне':'Rivne','луцьк':'Lutsk','миколаїв':'Mykolaiv','херсон':'Kherson','кривий ріг':'Kryvyi Rih'}
    place=aliases.get(place.lower(),place)
    data=get_json('https://geocoding-api.open-meteo.com/v1/search?'+urlencode({'name':place,'count':1,'language':'ru','format':'json'}))
    if not data.get('results'): raise ToolError('Не нашла населённый пункт. Укажи город и страну.')
    return data['results'][0]

WEATHER={0:'ясно',1:'преимущественно ясно',2:'переменная облачность',3:'пасмурно',45:'туман',48:'туман',51:'морось',53:'морось',55:'сильная морось',61:'небольшой дождь',63:'дождь',65:'сильный дождь',71:'снег',73:'снег',75:'сильный снег',80:'ливень',81:'ливень',82:'сильный ливень',95:'гроза',96:'гроза с градом',99:'гроза с градом'}

def weather(place):
    city=geocode(place)
    url='https://api.open-meteo.com/v1/forecast?'+urlencode({'latitude':city['latitude'],'longitude':city['longitude'],'current':'temperature_2m,apparent_temperature,weather_code,wind_speed_10m','daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max','forecast_days':3,'timezone':'auto'})
    data=get_json(url); current=data['current']; daily=data['daily']
    return {'kind':'weather','title':city['name']+', '+city.get('country',''),'temperature':current['temperature_2m'],'feels':current['apparent_temperature'],'wind':current['wind_speed_10m'],'condition':WEATHER.get(current['weather_code'],'переменная погода'),'time':current['time'],'timezone':data.get('timezone'),'days':[{'date':day,'min':daily['temperature_2m_min'][i],'max':daily['temperature_2m_max'][i],'rain':daily['precipitation_probability_max'][i]} for i,day in enumerate(daily['time'])], 'url':url,'source':'Open-Meteo · прогноз модели'}

def maps(query):
    url='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':query})
    result={'kind':'map','title':query,'url':url,'source':'Google Maps URL'}
    try:
        city=geocode(query); result.update({'latitude':city['latitude'],'longitude':city['longitude'],'location':city['name']+', '+city.get('country','')})
    except ToolError: pass
    return result

def parse_ip(query):
    """Accept one literal address, never a hostname, URL, range or scoped interface."""
    if not isinstance(query,str) or '%' in query:
        raise ToolError('Введи один IPv4 или IPv6, например 8.8.8.8.')
    try:
        address=ipaddress.ip_address(query.strip())
    except ValueError:
        raise ToolError('Введи один IPv4 или IPv6, например 8.8.8.8.') from None
    if not address.is_global or address.is_multicast or getattr(address,'ipv4_mapped',None) is not None:
        raise ToolError('Нужен публичный IP. У локальных и служебных адресов нет публичной геолокации.')
    return address

class IPMePage(HTMLParser):
    """Read only named table cells and the echoed query; ignore all page instructions."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ip=None; self.fields={}; self.cell=None; self.chunks=[]; self.label=None; self.in_row=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='input' and attrs.get('id')=='ip-lookup': self.ip=attrs.get('value')
        if tag=='tr': self.in_row=True; self.label=None
        if self.in_row and tag in ('th','td'): self.cell=tag; self.chunks=[]
    def handle_data(self,text):
        if self.cell: self.chunks.append(text)
    def handle_endtag(self,tag):
        if tag==self.cell:
            value=' '.join(' '.join(self.chunks).split())[:200]
            if tag=='th': self.label=value.rstrip(':').lower()
            elif self.label: self.fields[self.label]=None if value.lower() in ('none','null','n/a','-','') else value
            self.cell=None
        if tag=='tr': self.in_row=False; self.cell=None

def ip_lookup(query):
    address=parse_ip(query); ip=str(address)
    url='https://ip.me/ip/'+ip
    primary_error=None
    try:
        raw,final,_=fetch(url,limit=200_000)
        page=IPMePage(); page.feed(raw.decode('utf-8',errors='replace'))
        # Fail closed if a redirect or changed form returned a different IP's data.
        if not page.ip or ipaddress.ip_address(page.ip)!=address:
            raise ToolError('IP.me вернул данные другого адреса.')
        f=page.fields
        if not any(f.get(key) for key in ('country','organization','isp name','asn')):
            raise ToolError('IP.me не вернул сведения для этого адреса.')
        data={'country':f.get('country'),'country_code':f.get('country code'),'region':None,'city':f.get('city'),'latitude':f.get('latitude'),'longitude':f.get('longitude'),'org':f.get('organization'),'isp':f.get('isp name'),'asn':f.get('asn'),'timezone':None}
        source='IP.me'; url=final
    except (ToolError,ValueError) as exc:
        primary_error=str(exc)
        # One fixed, no-key backup. Only the validated literal IP leaves this device.
        url='https://ipwho.is/'+ip+'?lang=ru'
        try:
            response=get_json(url)
            if not isinstance(response,dict) or response.get('success') is not True or ipaddress.ip_address(response.get('ip',''))!=address:
                raise ToolError('Источник не вернул подтверждённых данных для этого IP.')
            connection=response.get('connection') or {}; zone=response.get('timezone') or {}
            data={key:response.get(key) for key in ('country','country_code','region','city','latitude','longitude')}
            data.update({key:connection.get(key) for key in ('org','isp','asn')}); data['timezone']=zone.get('id')
            source='IPWHOIS.IO'
        except (ToolError,ValueError,TypeError,AttributeError):
            raise ToolError('IP.me и резервный источник сейчас не дали данных. Попробуй позже.') from None
    # Strict display schema: missing information stays missing, never "None" or made-up coordinates.
    result={'kind':'ip','title':ip,'ip':ip,'version':'IPv'+str(address.version),'source':source,'url':url,'ipme_url':'https://ip.me/ip/'+ip,'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'approximate':True}
    for key in ('country','country_code','region','city','org','isp','timezone'):
        value=data.get(key); result[key]=value.strip()[:200] if isinstance(value,str) and value.strip() else None
    try: result['asn']=int(data.get('asn')) if 0<int(data.get('asn'))<=4294967295 else None
    except (ValueError,TypeError,OverflowError): result['asn']=None
    try:
        lat=float(data.get('latitude')); lon=float(data.get('longitude'))
        if -90<=lat<=90 and -180<=lon<=180: result.update(latitude=lat,longitude=lon)
    except (ValueError,TypeError,OverflowError): pass
    if primary_error: result['fallback_reason']=primary_error[:250]
    return result

def youtube_id(url):
    p=urlsplit(url); host=(p.hostname or '').lower()
    value=p.path.strip('/').split('/')[0] if host=='youtu.be' else parse_qs(p.query).get('v',[''])[0]
    if host in ('youtube.com','www.youtube.com','m.youtube.com') and p.path.startswith(('/shorts/','/embed/')): value=p.path.split('/')[2]
    return value if host in ('youtu.be','youtube.com','www.youtube.com','m.youtube.com') and re.fullmatch(r'[\w-]{11}',value,flags=re.ASCII) else None

def youtube(query):
    video=youtube_id(query)
    if video:
        data=get_json('https://www.youtube.com/oembed?'+urlencode({'url':'https://www.youtube.com/watch?v='+video,'format':'json'}))
        items=[{'title':data.get('title','Видео'),'id':video,'author':data.get('author_name',''),'url':'https://www.youtube.com/watch?v='+video}]
    else:
        raw,_,_=fetch('https://www.youtube.com/results?'+urlencode({'search_query':query}),limit=3_500_000)
        match=re.search(r'var ytInitialData\s*=\s*(\{.+?\});',raw.decode('utf-8',errors='replace'))
        if not match: raise ToolError('YouTube не отдал результаты без входа. Открой поиск по ссылке.')
        try: data=json.loads(match[1])
        except ValueError: raise ToolError('Не удалось прочитать выдачу YouTube.') from None
        items=[]; seen=set()
        def walk(value):
            if len(items)>=4: return
            if isinstance(value,dict):
                v=value.get('videoRenderer',{}); key=v.get('videoId')
                if key and re.fullmatch(r'[\w-]{11}',key,flags=re.ASCII) and key not in seen:
                    seen.add(key)
                    title=''.join(run.get('text','') for run in v.get('title',{}).get('runs',[]))
                    author=''.join(run.get('text','') for run in v.get('ownerText',{}).get('runs',[]))
                    items.append({'id':key,'title':title[:200],'author':author[:120],'url':'https://www.youtube.com/watch?v='+key})
                for child in value.values(): walk(child)
            elif isinstance(value,list):
                for child in value: walk(child)
        walk(data)
    if not items: raise ToolError('Не нашла доступных видео. Попробуй другой запрос.')
    return {'kind':'youtube','title':query,'items':items,'url':'https://www.youtube.com/results?'+urlencode({'search_query':query}),'source':'YouTube · названия и авторы, без просмотра содержимого видео'}

def plan(text, explicit=None):
    if explicit:
        if not isinstance(explicit,dict) or explicit.get('name') not in ('web','page','weather','youtube','maps','timer','ip'): raise ToolError('Неизвестный инструмент.')
        name=explicit['name']; query=explicit.get('query','')
        if not isinstance(query,str) or not query.strip() or len(query)>500: raise ToolError('Нужен запрос до 500 символов.')
        return name,query.strip()
    if re.search(r'\bне\s+(?:открывай|переходи|читай|ищи|шукай|відкривай|переходь|проверяй)|\b(?:do not|don.t)\s+(?:open|fetch|search|browse)|\bне\s+(?:используй|використовуй)\s+(?:интернет|інтернет)',text,re.I):
        return None
    plain=text.strip().rstrip('?!')
    if re.fullmatch(r'[\da-fA-F:.%]+',plain) and ('.' in plain or ':' in plain): return 'ip',plain
    urls=re.findall(r'https?://[^\s<>"\]\)]+',text)
    if urls:
        url=urls[0].rstrip('.,;!')
        parts=urlsplit(url)
        if parts.hostname in ('ip.me','www.ip.me') and parts.path.startswith('/ip/') and parts.path[4:]:
            return 'ip',parts.path[4:].rstrip('/')
        if parts.hostname in ('ip.me','www.ip.me') and parts.path in ('/ip','/ip/'):
            return 'page','https://ip.me/ip'
        return ('youtube' if youtube_id(url) else 'page'),url
    ip_intent=re.search(r'(?:\bip(?:v[46])?(?:[ -]адрес)?|\bип(?:[ -]адрес)?|\bайпи)\b\s*[:—-]?\s*([\da-fA-F:.%]+)(?=$|\s|[,;!?])',text,re.I)
    if ip_intent and re.search(r'[\d.:]',ip_intent[1]): return 'ip',ip_intent[1].rstrip('.')
    command=re.fullmatch(r'(?:проверь|перевір|пробей|пробий|найди|знайди|check|lookup)\s+([\da-fA-F:.]+)[?!]?',text.strip(),re.I)
    if command and ('.' in command[1] or ':' in command[1]): return 'ip',command[1]
    map_suffix=re.search(r'(.{2,120})\s+(?:на|в)\s+(?:гугл\s*)?карт(?:е|і|ах)\s*[.!?]?$',text,re.I)
    if map_suffix:
        return 'maps',re.sub(r'^(?:покажи|найди|знайди|відкрий|открой)\s+','',map_suffix[1],flags=re.I).strip()
    patterns=[('timer',r'(?:таймер|засеки|засічи)\s*(?:на\s*)?(.+)'),('weather',r'(?:погод[ауіы]|weather)\s*(?:сегодня|завтра|зараз|сейчас)?\s*(?:в|у|для|in)?\s+(.+)'),('youtube',r'(?:ютуб[еі]?|youtube)\s*[:—-]?\s+(.+)'),('maps',r'(?:на\s+карт[еі]|гугл\s*карт(?:ы|и|ах)|google\s*maps|карта|карти|карты)\s*[:—-]?\s+(.+)'),('web',r'(?:найди|знайди|поищи|пошукай|пошук|пошукати|погугли|загугли|ищи|search)\s+(?:в\s+интернете\s+|в\s+інтернеті\s+)?(.+)')]
    for name,pattern in patterns:
        found=re.search(pattern,text,re.I)
        if found: return name,found[1].strip(' ?.!')[:500]
    return None

def run(name,query):
    if name=='ip': return ip_lookup(query)
    if name=='web': return search_web(query)
    if name=='page': return {'kind':'web','title':'Чтение страницы','items':[read_page(query)],'url':query,'source':'Открытая веб-страница'}
    if name=='weather': return weather(query)
    if name=='youtube': return youtube(query)
    if name=='maps': return maps(query)
    if name=='timer':
        found=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(с|сек\w*|s|м|мин\w*|хв\w*|m|ч|час\w*|год\w*|h)?\s*',query,re.I)
        if not found: raise ToolError('Напиши длительность: например, «таймер на 5 минут» или «таймер 30 секунд».')
        amount=float(found[1].replace(',','.')); unit=(found[2] or 'мин').lower()
        seconds=round(amount*(3600 if unit.startswith(('ч','год','h')) else 1 if unit.startswith(('с','s')) else 60))
        if not 1<=seconds<=86400: raise ToolError('Таймер — от 1 секунды до 24 часов.')
        return {'kind':'timer','seconds':seconds,'title':query,'source':'Таймер этой вкладки'}
    raise ToolError('Неизвестный инструмент.')
