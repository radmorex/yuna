//! Навыки Юны для владельца: только пассивные запросы к публичным сервисам и фиксированные команды.
//! Запускаются лишь с локальной страницы (`platform == "control"`), зрителям недоступны.
use serde_json::Value;
use std::net::IpAddr;
use std::time::Duration;

#[derive(Debug, PartialEq, Clone)]
pub enum Skill {
    Weather(String),
    Timer(u64),
    Search(String),
    IpInfo(IpAddr),
    Domain(String),
    Indicators(String),
    Spotify(SpotifyCmd),
    /// Инструмент osint-kit или public-tools через `tools/arsenal.py`.
    Arsenal { tool: &'static str, target: String },
    /// Открыть адрес в браузере по умолчанию.
    OpenUrl(String),
    /// Открыть установленную программу. Имя потом ищется только среди приложений.
    OpenApp(String),
    /// Найти видео на YouTube и запустить первое. Пустой запрос: выбирает сама.
    PlayVideo(String),
    /// Запустить в Spotify проверенный URI (`spotify:playlist:…`).
    SpotifyUri(String),
    /// Сохранённый плейлист: по имени или основной.
    SpotifyPlaylist(Option<String>),
    /// Владелец просто прислал ссылку: включить, а плейлист запомнить основным, если основного ещё нет.
    SpotifyLink(String),
    /// Запомнить плейлист, чтобы потом запускать его словами.
    SpotifySave { name: Option<String>, uri: String },
    /// Показать поиск в приложении Spotify. Запустить результат без Web API нельзя.
    SpotifySearch(String),
    /// Команду поняли, но выполнить её нельзя: объяснение вместо выдуманного результата.
    Explain(&'static str),
    /// Состояние Mullvad VPN или подключение к нему.
    Vpn { connect: bool },
    /// Запомнить, куда писать и звонить: `@ник` или `+номер`.
    SaveContact { name: String, address: String },
    /// Запомнить голосовой канал Discord по ссылке.
    SaveVoice { name: String, link: String },
    /// Написать в Telegram через Forkgram: чат с готовым текстом и сразу отправка.
    /// `to`: один или несколько получателей через пробел.
    TgDraft { to: String, text: String },
    /// То же, но текст придумывает сама. `about`: о чём, может быть пусто. Конвейер превращает это в `TgDraft`.
    TgCompose { to: String, about: String },
    /// Позвонить. `telegram`: просили именно в Telegram.
    Call { to: String, telegram: bool },
    /// Открыть голосовой канал Discord: по имени из книги или по ссылке.
    DiscordVoice(String),
    /// Сказать текст через заданное время. Живёт, пока запущена Юна.
    Remind { secs: u64, text: String },
    /// Системная громкость.
    Volume(VolumeCmd),
    /// Закрыть установленную программу.
    QuitApp(String),
    /// Посмотреть на экран. Снимок делает конвейер и отдаёт модели как фото.
    ScreenLook,
    /// Список команд, без модели.
    Help,
    /// Пауза или продолжение видео YouTube в открытой вкладке браузера.
    Video { play: bool },
    /// Что за компьютер: процессор, память, система, батарея, диск.
    SysInfo,
}

#[derive(Debug, PartialEq, Clone, Copy)]
pub enum VolumeCmd {
    Set(u8),
    Up,
    Down,
    Mute(bool),
}

#[derive(Debug, PartialEq, Clone, Copy)]
pub enum SpotifyCmd {
    Pause,
    Play,
    Next,
    Previous,
    Current,
}

fn after<'a>(q: &'a str, low: &str, keys: &[&str]) -> Option<&'a str> {
    keys.iter().find_map(|k| low.find(k).map(|i| q.get(i + k.len()..).unwrap_or("").trim()))
}

/// Домен: только буквы, цифры, дефисы и хотя бы одна точка.
pub fn valid_domain(d: &str) -> bool {
    d.len() <= 253 && d.contains('.') && !d.starts_with('.') && !d.ends_with('.')
        && d.split('.').all(|p| !p.is_empty() && p.len() <= 63 && !p.starts_with('-') && !p.ends_with('-')
            && p.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
        && d.rsplit('.').next().is_some_and(|tld| tld.chars().any(|c| c.is_ascii_alphabetic()))
}

/// Только публичные адреса: локальные и внутренние никуда не отправляем.
pub fn public_ip(ip: &IpAddr) -> bool {
    match ip {
        IpAddr::V4(v) => !(v.is_private() || v.is_loopback() || v.is_link_local() || v.is_multicast() || v.is_unspecified() || v.is_broadcast()),
        IpAddr::V6(v) => {
            let first = v.segments()[0];
            !(v.is_loopback() || v.is_multicast() || v.is_unspecified() || (first & 0xfe00) == 0xfc00 || (first & 0xffc0) == 0xfe80)
        }
    }
}

fn first_url(q: &str) -> Option<String> {
    q.split_whitespace()
        .map(|w| w.trim_matches(|c: char| ",;!?«»\"'()".contains(c)).trim_end_matches('.'))
        .find(|w| w.starts_with("http://") || w.starts_with("https://"))
        .map(String::from)
}

fn first_domain(q: &str) -> Option<String> {
    q.split_whitespace()
        .map(|w| w.trim_matches(|c: char| ",;:!?«»\"'()".contains(c)).trim_end_matches('.').to_lowercase())
        .find(|w| valid_domain(w))
}

fn arsenal(tool: &'static str, target: impl Into<String>) -> Option<Skill> {
    Some(Skill::Arsenal { tool, target: target.into() })
}

/// Запросы к арсеналу osint-kit и public-tools.
fn parse_arsenal(q: &str, low: &str) -> Option<Skill> {
    let has = |keys: &[&str]| keys.iter().any(|k| low.contains(k));
    if let Some(url) = first_url(q) {
        if has(&["заголов", "редирект", "headers"]) { return arsenal("headers", url); }
        if has(&["посилання зі", "посилання з", "ссылки с", "контакти", "контакты"]) { return arsenal("links", url); }
        if has(&["таблиц"]) { return arsenal("tables", url); }
        if has(&["вебарх", "веб-арх", "wayback", "архів", "архив"]) { return arsenal("wayback", url); }
        if has(&["репутац", "вірус", "вирус", "безпечн", "безопасн"]) { return arsenal("urlcheck", url); }
        if has(&["прочитай", "прочитати", "що на сторінці", "перекажи"]) { return arsenal("page", url); }
    }
    if has(&["вебарх", "веб-арх", "wayback"]) {
        if let Some(d) = first_domain(q) { return arsenal("wayback", d); }
    }
    if has(&["домен", "whois", "піддомен", "поддомен"]) {
        if let Some(d) = first_domain(q) { return arsenal("domain", d); }
    }
    if has(&["хеш", "hash"]) {
        if let Some(h) = q.split_whitespace().find(|w| matches!(w.len(), 32 | 40 | 64) && w.chars().all(|c| c.is_ascii_hexdigit())) {
            return arsenal("hash", h);
        }
    }
    // Только как команда: «вчора дивився ютуб цілий день» и «у мене карта банку» поиском не считаются.
    let cmd = ["знайди ", "найди ", "пошукай ", "шукай ", "покажи "].iter().find_map(|v| low.strip_prefix(v)).unwrap_or(low);
    let tail = |keys: &[&str]| {
        let t = keys.iter().find_map(|k| cmd.strip_prefix(k))?.trim();
        let original = q.get(q.len().checked_sub(t.len())?..).unwrap_or(t);
        (!t.is_empty()).then(|| original.trim_end_matches(|c: char| "?!.".contains(c)).to_string())
    };
    if let Some(t) = tail(&["youtube ", "ютуб ", "ютубі ", "на ютубі ", "в ютубі ", "на youtube "]) {
        return arsenal("youtube", t);
    }
    if let Some(t) = tail(&["на карті ", "на карте ", "карта ", "карті "]) {
        return arsenal("maps", t);
    }
    if let Some(t) = after(q, low, &["пошукові запити", "поисковые запросы", "дорки"]) {
        let t = t.trim_start_matches(|c: char| ":-— ".contains(c));
        if !t.is_empty() { return arsenal("dorks", t); }
    }
    None
}

/// Ссылка `open.spotify.com/{playlist,album,track}/ID` → URI. Всё остальное отклоняется.
pub fn spotify_uri(url: &str) -> Option<String> {
    let rest = url.strip_prefix("https://open.spotify.com/")?;
    let mut parts = rest.split('?').next()?.split('/').filter(|p| !p.is_empty() && !p.starts_with("intl-"));
    let (kind, id) = (parts.next()?, parts.next()?);
    let ok = matches!(kind, "playlist" | "album" | "track") && id.len() == 22 && id.chars().all(|c| c.is_ascii_alphanumeric());
    ok.then(|| format!("spotify:{kind}:{id}"))
}

fn valid_spotify_uri(uri: &str) -> bool {
    let mut p = uri.split(':');
    p.next() == Some("spotify")
        && matches!(p.next(), Some("playlist" | "album" | "track"))
        && p.next().is_some_and(|id| id.len() == 22 && id.chars().all(|c| c.is_ascii_alphanumeric()))
        && p.next().is_none()
}

fn parse_vpn(low: &str) -> Option<Skill> {
    if !["впн", "vpn", "мулвад", "mullvad"].iter().any(|k| low.contains(k)) {
        return None;
    }
    if stop_verb_first(low) {
        return Some(Skill::Explain("вимикати VPN я не буду: без нього не працює інкогніто. Вимкни його сам у застосунку Mullvad"));
    }
    let first = low.split_whitespace().next().unwrap_or("");
    if matches!(first, "увімкни" | "включи" | "підключи" | "запусти" | "подключи") && !low.contains("браузер") {
        return Some(Skill::Vpn { connect: true });
    }
    let asks = ["перевір", "проверь", "статус", "який", "какой", "чи ", "де ", "працює", "работает"].iter().any(|k| low.contains(k));
    (asks && !low.contains("браузер")).then_some(Skill::Vpn { connect: false })
}

/// Нагадування, гучність, закриття програм, екран і довідка.
fn parse_convenience(q: &str, low: &str) -> Option<Skill> {
    let t = low.trim().trim_end_matches(|c: char| ".!?".contains(c));
    // Без пробелов: речь слышится и как «щоти вмієш». По началу: «що ти вмієш робити» тоже просьба о списке.
    let joined: String = t.chars().filter(|c| !c.is_whitespace()).collect();
    if ["щотивмієш", "щовмієш", "щотиможеш", "чтотыумеешь", "чтоумеешь"].iter().any(|k| joined.starts_with(k)) || ["допомога", "команди", "список команд", "help"].contains(&t) {
        return Some(Skill::Help);
    }
    if ["що на екрані", "що в мене на екрані", "що у мене на екрані", "подивись на екран", "глянь на екран", "прочитай екран", "що ти бачиш на екрані", "что на экране", "посмотри на экран"].iter().any(|k| t.contains(k)) {
        return Some(Skill::ScreenLook);
    }
    const ABOUT_PC: &[&str] = &["за комп", "який комп", "який процесор", "що за процесор", "який чіп", "який чип", "який макбук", "що за макбук", "який мак", "що за мак", "скільки оперативк", "скільки пам'ят", "скільки памят", "яка оперативк", "яка версія macos", "яка версія мак", "заряд батаре", "скільки заряду", "батаре", "скільки місця", "вільного місця", "характеристик", "какой процессор", "что за комп", "какой комп", "сколько оперативк", "сколько памят"];
    if ABOUT_PC.iter().any(|k| t.contains(k)) {
        return Some(Skill::SysInfo);
    }
    let first = t.split_whitespace().next().unwrap_or("");
    if matches!(first, "нагадай" | "нагадуй" | "напомни") {
        return Some(parse_remind(q));
    }
    if matches!(first, "закрий" | "закрой") {
        let name = strip_words(q.split_once(char::is_whitespace).map_or("", |(_, r)| r), &["програму", "программу", "застосунок", "приложение"]);
        let name = name.trim_end_matches(|c: char| ".!?".contains(c)).trim();
        return Some(if name.is_empty() { Skill::Explain("скажи яку програму закрити: закрий Safari") } else { Skill::QuitApp(name.to_string()) });
    }
    if matches!(t, "вимкни звук" | "виключи звук" | "выключи звук" | "без звуку") {
        return Some(Skill::Volume(VolumeCmd::Mute(true)));
    }
    if matches!(t, "увімкни звук" | "включи звук") {
        return Some(Skill::Volume(VolumeCmd::Mute(false)));
    }
    if matches!(t, "голосніше" | "гучніше" | "громче" | "зроби голосніше" | "зроби гучніше" | "сделай громче") {
        return Some(Skill::Volume(VolumeCmd::Up));
    }
    if matches!(t, "тихіше" | "тише" | "зроби тихіше" | "сделай тише") {
        return Some(Skill::Volume(VolumeCmd::Down));
    }
    if t.contains("гучн") || t.contains("громкост") {
        let n = t.split(|c: char| !c.is_ascii_digit()).find_map(|w| w.parse::<u32>().ok())?;
        return Some(if n <= 100 { Skill::Volume(VolumeCmd::Set(n as u8)) } else { Skill::Explain("гучність буває від 0 до 100") });
    }
    None
}

/// «Нагадай через 1 годину 30 хвилин подзвонити мамі».
fn parse_remind(q: &str) -> Skill {
    const USAGE: &str = "скажи коли й про що: нагадай через 10 хвилин подзвонити";
    let words: Vec<&str> = q.split_whitespace().collect();
    let Some(at) = words.iter().position(|w| clean_word(w) == "через") else {
        return Skill::Explain(USAGE);
    };
    let unit = |w: &str| -> Option<u64> {
        if w.starts_with("сек") { Some(1) } else if w.starts_with("хв") || w.starts_with("мин") { Some(60) } else if w.starts_with("год") || w.starts_with("час") { Some(3600) } else { None }
    };
    let mut secs = 0u64;
    let mut i = at + 1;
    while let Some(w) = words.get(i).map(|w| clean_word(w)) {
        if let Ok(n) = w.parse::<u64>() {
            // Число без единицы считаем минутами, как в таймере.
            let next = words.get(i + 1).map(|w| clean_word(w)).and_then(|w| unit(&w));
            secs = secs.saturating_add(n.saturating_mul(next.unwrap_or(60)));
            i += if next.is_some() { 2 } else { 1 };
        } else if matches!(w.as_str(), "півгодини" | "полчаса") {
            secs += 1800;
            i += 1;
        } else if w == "пів" && words.get(i + 1).is_some_and(|n| clean_word(n).starts_with("год")) {
            secs += 1800;
            i += 2;
        } else if let Some(k) = unit(&w) {
            secs += k;
            i += 1;
        } else if w == "і" || w == "й" {
            i += 1;
        } else {
            break;
        }
    }
    if !(1..=86_400).contains(&secs) {
        return Skill::Explain("нагадати можу в межах доби: нагадай через 10 хвилин подзвонити");
    }
    let before = words[1..at].iter().filter(|w| !matches!(clean_word(w).as_str(), "мені" | "мне")).copied().collect::<Vec<_>>().join(" ");
    let text = format!("{before} {}", words[i..].join(" "));
    let text = text.trim();
    let text = ["що ", "про ", "щоб ", "чтобы ", "что "].iter().find_map(|p| text.strip_prefix(p)).unwrap_or(text);
    Skill::Remind { secs, text: if text.is_empty() { "час вийшов".to_string() } else { crate::util::truncate_chars(text, 200) } }
}

const HELP_TEXT: &str = "Комп'ютер: відкрий ютуб, відкрий калькулятор, закрий Safari, гучність 30, тихіше, вимкни звук
Відео й музика: запусти відео про космос, відкрий щось цікаве, постав ютуб на паузу, продовж відео, увімкни музику, вимкни музику, наступний трек, що грає, запусти мій плейлист
Повідомлення: напиши @нік привіт, напиши щось @нік, запам'ятай контакт Саша @нік, подзвони Саші
Discord: запам'ятай войс друзі <посилання>, зайди в войс друзі
Час: таймер на 5 хвилин, нагадай через 10 хвилин подзвонити
Пошук: знайди рецепт борщу, погода у Києві, прочитай <посилання>, whois example.com, перевір впн
Екран і фото: що на екрані, кнопка Фото
Про комп'ютер: який процесор, скільки оперативки, заряд батареї, скільки місця
Вгору/вниз у полі вводу: твої минулі команди";

/// Закрывать нельзя: это сама Юна и её окружение, а без VPN не работает инкогнито.
const NO_QUIT_APPS: &[&str] = &["claude", "finder", "mullvad vpn"];

async fn volume(cmd: VolumeCmd) -> Result<String, String> {
    let change = match cmd {
        VolumeCmd::Set(n) => format!("set volume output volume {}\nset volume output muted false", n.min(100)),
        VolumeCmd::Up => "set volume output volume ((output volume of (get volume settings)) + 10)\nset volume output muted false".to_string(),
        VolumeCmd::Down => "set volume output volume ((output volume of (get volume settings)) - 10)".to_string(),
        VolumeCmd::Mute(on) => format!("set volume output muted {on}"),
    };
    let script = format!("{change}\nset s to get volume settings\nreturn (output volume of s as text) & \" \" & (output muted of s as text)");
    let out = tokio::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).kill_on_drop(true).output().await.map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err("macOS не змінила гучність".into());
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut parts = text.split_whitespace();
    let (level, muted) = (parts.next().unwrap_or("?"), parts.next() == Some("true"));
    Ok(if muted { "Звук вимкнено".to_string() } else { format!("Гучність {level}%") })
}

async fn quit_app(name: &str) -> Result<String, String> {
    let app = resolve_app(name, &app_dirs()).map_err(|e| {
        if e.contains("не відкриваю") { format!("{name}: цю програму я не чіпаю") } else { format!("не знайшла програму «{name}». Вкладки й сайти закривати не вмію") }
    })?;
    let title = app.file_stem().unwrap_or_default().to_string_lossy().to_string();
    if NO_QUIT_APPS.contains(&title.to_lowercase().as_str()) {
        return Err(format!("{title} я не закриваю"));
    }
    // Проверка обязательна: `tell … to quit` сначала запустил бы закрытую программу. Несохранённое macOS спросит сама.
    let script = "on run argv\nset n to item 1 of argv\nif application n is running then\ntell application n to quit\nreturn \"closed\"\nend if\nreturn \"idle\"\nend run";
    let out = tokio::time::timeout(Duration::from_secs(10), tokio::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).arg(&title).kill_on_drop(true).output())
        .await
        .map_err(|_| format!("{title} не закривається: можливо, чекає на твоє рішення"))?
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!("{title} не закрилась"));
    }
    Ok(if String::from_utf8_lossy(&out.stdout).trim() == "idle" { format!("{title} і так не запущена") } else { format!("Закрила {title}") })
}

fn base64_encode(data: &[u8]) -> String {
    const ABC: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let n = (u32::from(chunk[0]) << 16) | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8) | u32::from(*chunk.get(2).unwrap_or(&0));
        out.push(ABC[(n >> 18) as usize & 63] as char);
        out.push(ABC[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { ABC[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { ABC[n as usize & 63] as char } else { '=' });
    }
    out
}

/// Снимок экрана как data URL. Файл удаляется сразу, на диске ничего не остаётся.
pub async fn capture_screen() -> Result<String, String> {
    let path = std::env::temp_dir().join(format!("yuna-screen-{}.jpg", crate::util::now_ms()));
    let shot = tokio::process::Command::new("/usr/sbin/screencapture").args(["-x", "-t", "jpg"]).arg(&path).output().await;
    let resized = tokio::process::Command::new("/usr/bin/sips").args(["-Z", "1600", "-s", "formatOptions", "70"]).arg(&path).output().await;
    let bytes = tokio::fs::read(&path).await;
    let _ = tokio::fs::remove_file(&path).await;
    let failed = "не вдалося зняти екран: дозволь Запис екрана в Налаштуваннях, Конфіденційність";
    if !shot.is_ok_and(|o| o.status.success()) || !resized.is_ok_and(|o| o.status.success()) {
        return Err(failed.to_string());
    }
    let url = format!("data:image/jpeg;base64,{}", base64_encode(&bytes.map_err(|_| failed.to_string())?));
    crate::event::parse_image(&url).ok_or("знімок екрана завеликий")?;
    Ok(url)
}

/// Уведомление macOS: окно Юны может быть свёрнуто. Текст идёт аргументом, а не в текст сценария.
pub async fn notify(text: &str) {
    let script = "on run argv\ndisplay notification (item 1 of argv) with title \"Юна\"\nend run";
    let _ = tokio::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).arg(text).output().await;
}

const TG_WORDS: &[&str] = &["тг", "телеграм", "телеграмі", "телеграме", "телеграму", "telegram", "форкграм", "форкграмі", "форкграме", "форкграму", "forkgram"];

fn clean_word(w: &str) -> String {
    w.trim_matches(|c: char| ",.:;!?«»\"".contains(c)).to_lowercase()
}

/// Убирает «в тг» из любого места фразы. `None`: Telegram не упомянут.
fn without_tg(q: &str) -> Option<String> {
    let words: Vec<&str> = q.split_whitespace().collect();
    let at = words.iter().position(|w| TG_WORDS.contains(&clean_word(w).as_str()))?;
    let from = if at > 0 && matches!(clean_word(words[at - 1]).as_str(), "в" | "у" | "на") { at - 1 } else { at };
    Some(words[..from].iter().chain(&words[at + 1..]).copied().collect::<Vec<_>>().join(" "))
}

/// Telegram, звонки и Discord. Глагол обязателен в начале, поэтому рассказ «я написав у тг» ничего не шлёт.
fn parse_comms(q: &str, low: &str) -> Option<Skill> {
    let first = low.split_whitespace().next().unwrap_or("");
    let words: Vec<&str> = q.split_whitespace().collect();
    if SAVE_VERBS.iter().any(|k| first.starts_with(k)) {
        let kind = words.get(1).map(|w| clean_word(w)).unwrap_or_default();
        if kind == "контакт" {
            let (name, address) = (words.get(2)?, words.get(3).map(|w| w.trim_matches(|c: char| ",.".contains(c))));
            return Some(match address.and_then(valid_address) {
                Some(address) if words.len() == 4 => Skill::SaveContact { name: name.to_lowercase(), address },
                _ => Skill::Explain("скажи так: запам'ятай контакт Саша @нік або запам'ятай контакт Саша +380991234567"),
            });
        }
        if matches!(kind.as_str(), "войс" | "канал") {
            let link = words.iter().find_map(|w| discord_channel(w));
            return Some(match (link, words.get(2)) {
                (Some(link), Some(name)) if discord_channel(name).is_none() => Skill::SaveVoice { name: name.to_lowercase(), link },
                _ => Skill::Explain("скажи так: запам'ятай войс назва і встав посилання https://discord.com/channels/… (правий клік по каналу, Копіювати посилання)"),
            });
        }
        return None;
    }
    const SEND: &[&str] = &["напиши", "надішли", "відправ", "отправь"];
    // «@нік напиши йому привіт», «@один @другий напиши…»: получатели стоят первыми.
    if first.starts_with('@') {
        let verb = words.iter().position(|w| SEND.contains(&clean_word(w).as_str()))?;
        if !words[..verb].iter().all(|w| w.starts_with('@') || is_connector(w)) {
            return None;
        }
        let rest = words[..verb].iter().chain(&words[verb + 1..]).copied().collect::<Vec<_>>().join(" ");
        let rest = without_tg(&rest).unwrap_or(rest);
        return Some(tg_to_parts(&rest.split_whitespace().collect::<Vec<_>>(), None));
    }
    // «Придумай щось і напиши @нік» и «напиши щось @нік» значат одно и то же.
    let invent = first == "придумай" && words.iter().any(|w| clean_word(w) == "напиши");
    if invent || SEND.contains(&first) {
        let after_verb = if invent { words.iter().position(|w| clean_word(w) == "напиши")? + 1 } else { 1 };
        let rest = words[after_verb..].join(" ");
        let cleaned = without_tg(&rest);
        let has_tg = cleaned.is_some();
        let rest = cleaned.unwrap_or(rest);
        let parts: Vec<&str> = rest.split_whitespace().collect();
        if !parts.iter().any(|w| w.starts_with('@')) {
            let at = usize::from(parts.first().is_some_and(|w| COMPOSE_WORDS.contains(&clean_word(w).as_str())));
            let Some(name) = parts.get(at) else {
                return has_tg.then_some(Skill::Explain("скажи кому: напиши в тг @нік привіт"));
            };
            // Для имени нужно «в тг» или контакт в книге, иначе «напиши вірш» стал бы сообщением.
            if !has_tg {
                find_contact(&load_book(std::path::Path::new(BOOK_FILE)), name).ok()?;
            }
        }
        if !invent {
            return Some(tg_to_parts(&parts, None));
        }
        // Тема могла стоять до «напиши»: «придумай щось про каву і напиши».
        let before = strip_words(&words[1..after_verb - 1].join(" "), &["щось", "що-небудь", "і", "й", "та"]);
        return Some(tg_to_parts(&parts, Some(&before)));
    }
    if matches!(first, "подзвони" | "зателефонуй" | "набери" | "позвони" | "звони") {
        let rest = words[1..].join(" ");
        let (rest, telegram) = without_tg(&rest).map_or((rest.clone(), false), |r| (r, true));
        let to = rest.split_whitespace().next().unwrap_or("").trim_matches(|c: char| ",.!?".contains(c)).to_string();
        // Распознавание иногда превращает «зупини музику» в звонок, а звонить музыке незачем.
        if to.to_lowercase().starts_with("музик") {
            return Some(Skill::Spotify(SpotifyCmd::Pause));
        }
        return Some(if to.is_empty() { Skill::Explain("скажи кому подзвонити: подзвони Саші") } else { Skill::Call { to, telegram } });
    }
    if matches!(first, "зайди" | "зайти" | "заходь" | "підключись" | "приєднайся" | "подключись") && ["войс", "голосов", "дс", "дискорд", "діскорд", "discord"].iter().any(|k| low.contains(k)) {
        if let Some(link) = words.iter().find_map(|w| discord_channel(w)) {
            return Some(Skill::DiscordVoice(link));
        }
        let name = strip_words(&words[1..].join(" "), &["в", "у", "на", "дс", "дискорд*", "діскорд*", "discord*", "войс*", "голосов*", "канал*", "сервер*", "сервак*"]);
        return Some(if name.is_empty() {
            Skill::Explain("скажи в який канал: зайди в войс назва. Спершу запам'ятай його: запам'ятай войс назва і встав посилання на канал")
        } else {
            Skill::DiscordVoice(name.to_lowercase())
        });
    }
    None
}

fn is_connector(w: &str) -> bool {
    matches!(clean_word(w).as_str(), "і" | "й" | "та" | "и")
}

/// Выделяет получателей из слов команды: все ники или имя, плюс «і Олегу» после него.
/// `invent`: текст придумывает сама, значение добавляется к теме.
fn tg_to_parts(parts: &[&str], invent: Option<&str>) -> Skill {
    let trim = |w: &str| w.trim_matches(|c: char| ",.:;".contains(c)).to_string();
    let mut taken = vec![false; parts.len()];
    let mut to = Vec::new();
    if parts.iter().any(|w| w.starts_with('@')) {
        for (i, w) in parts.iter().enumerate() {
            if w.starts_with('@') {
                taken[i] = true;
                to.push(trim(w));
            } else if is_connector(w) && i > 0 && parts[i - 1].starts_with('@') && parts.get(i + 1).is_some_and(|n| n.starts_with('@')) {
                taken[i] = true;
            }
        }
    } else {
        let mut at = usize::from(parts.first().is_some_and(|w| COMPOSE_WORDS.contains(&clean_word(w).as_str())));
        if let Some(name) = parts.get(at) {
            taken[at] = true;
            to.push(trim(name));
            let book = load_book(std::path::Path::new(BOOK_FILE));
            while parts.get(at + 1).is_some_and(|w| is_connector(w)) && parts.get(at + 2).is_some_and(|n| find_contact(&book, &trim(n)).is_ok()) {
                taken[at + 1] = true;
                taken[at + 2] = true;
                to.push(trim(parts[at + 2]));
                at += 2;
            }
        }
    }
    let text = parts.iter().zip(&taken).filter(|(_, t)| !**t).map(|(w, _)| *w).collect::<Vec<_>>().join(" ");
    let text = match invent {
        Some(before) => format!("щось {before} {}", strip_words(&text, &["щось", "що-небудь"])),
        None => text,
    };
    tg_draft(&to.join(" "), &text)
}

const COMPOSE_WORDS: &[&str] = &["щось", "що-небудь", "що-небудь", "чтонибудь", "что-нибудь", "что-то"];

fn tg_draft(to: &str, text: &str) -> Skill {
    const PRONOUNS: &[&str] = &["йому", "їй", "їм", "ему", "ей", "им"];
    let to = to.trim_matches(|c: char| ",:".contains(c));
    let mut text = text.trim().trim_start_matches(|c: char| ",: ".contains(c));
    if let Some((word, rest)) = text.split_once(char::is_whitespace) {
        if PRONOUNS.contains(&clean_word(word).as_str()) {
            text = rest.trim_start();
        }
    }
    let text = ["що ", "что "].iter().find_map(|p| text.strip_prefix(p)).unwrap_or(text);
    let (head, about) = text.split_once(char::is_whitespace).unwrap_or((text, ""));
    if COMPOSE_WORDS.contains(&clean_word(head).as_str()) && !to.is_empty() && !PRONOUNS.contains(&clean_word(to).as_str()) {
        return Skill::TgCompose { to: to.to_string(), about: crate::util::truncate_chars(about.trim(), 200) };
    }
    if PRONOUNS.contains(&clean_word(to).as_str()) {
        return Skill::Explain("скажи кому саме: напиши в тг @нік привіт або напиши в тг Саші привіт");
    }
    if to.is_empty() || text.is_empty() {
        return Skill::Explain("скажи кому й що: напиши в тг Саші привіт");
    }
    Skill::TgDraft { to: to.to_string(), text: crate::util::truncate_chars(text, 1000) }
}

/// `@ник` Telegram или номер в международном формате. Всё остальное отклоняется: значение попадает в ссылку.
fn valid_address(a: &str) -> Option<String> {
    if let Some(nick) = a.strip_prefix('@') {
        return ((5..=32).contains(&nick.len()) && nick.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')).then(|| a.to_string());
    }
    let digits = a.strip_prefix('+')?;
    ((7..=15).contains(&digits.len()) && digits.chars().all(|c| c.is_ascii_digit())).then(|| a.to_string())
}

/// `https://discord.com/channels/<сервер>/<канал>` → `<сервер>/<канал>`, только цифры.
fn discord_channel(url: &str) -> Option<String> {
    let rest = url.trim_matches(|c: char| ",.«»".contains(c)).strip_prefix("https://")?;
    let rest = ["discord.com/channels/", "ptb.discord.com/channels/", "canary.discord.com/channels/"].iter().find_map(|p| rest.strip_prefix(p))?;
    let (guild, channel) = rest.trim_end_matches('/').split_once('/')?;
    let numeric = |s: &str| (15..=22).contains(&s.len()) && s.chars().all(|c| c.is_ascii_digit());
    (numeric(guild) && numeric(channel)).then(|| format!("{guild}/{channel}"))
}

const BOOK_FILE: &str = "data/contacts.json";

/// Записная книжка Юны: только то, что владелец сам ей сказал. Лежит локально, в git и в модель целиком не идёт.
#[derive(Default, serde::Serialize, serde::Deserialize)]
struct Book {
    #[serde(default)]
    contacts: std::collections::HashMap<String, Vec<String>>,
    #[serde(default)]
    voices: std::collections::HashMap<String, String>,
}

fn load_book(path: &std::path::Path) -> Book {
    std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn save_book(path: &std::path::Path, book: &Book) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::write(path, serde_json::to_vec_pretty(book).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())
}

/// Ищет контакт с учётом падежа: «Саші» находит «саша», но только если подходит ровно один.
fn find_contact(book: &Book, word: &str) -> Result<Vec<String>, String> {
    if let Some(address) = valid_address(word) {
        return Ok(vec![address]);
    }
    let low = word.to_lowercase();
    if let Some(found) = book.contacts.get(&low) {
        return Ok(found.clone());
    }
    // Падеж меняет только конец слова (Саша/Саші, Іван/Івану), поэтому ищем самое длинное общее начало.
    // Остаток слова обязан быть окончанием: сообщение уходит сразу, и «вірш» не должен найти контакт «Віра».
    const ENDINGS: &[&str] = &["", "а", "я", "у", "ю", "і", "ї", "е", "є", "о", "ові", "еві", "єві", "ві", "ому", "ем", "ом"];
    let common = |name: &str| -> usize {
        let shared = name.chars().zip(low.chars()).take_while(|(a, b)| a == b).count();
        let word_tail: String = low.chars().skip(shared).collect();
        if shared >= 2 && name.chars().count() <= shared + 1 && ENDINGS.contains(&word_tail.as_str()) { shared } else { 0 }
    };
    let best = book.contacts.keys().map(|name| common(name)).max().unwrap_or(0);
    let matches: Vec<_> = book.contacts.iter().filter(|(name, _)| best > 0 && common(name) == best).collect();
    match matches.as_slice() {
        [(_, found)] => Ok((*found).clone()),
        [] => Err(format!("контакта «{word}» я не знаю. Скажи: запам'ятай контакт Ім'я @нік або напиши одразу на @нік")),
        _ => Err(format!("«{word}» підходить кільком контактам, назви точніше")),
    }
}

/// Клиент Telegram у владельца: Forkgram. Другие клиенты намеренно не ищем.
fn telegram_installed() -> Option<std::path::PathBuf> {
    resolve_app("forkgram", &app_dirs()).ok()
}

/// Открывает чат именно в Forkgram: схему `tg:` может перехватить другой клиент. Без него идём на t.me в браузере.
async fn open_chat(address: &str, text: Option<&str>) -> Result<bool, String> {
    let app = telegram_installed();
    let link = telegram_link(address, text, app.is_some());
    match &app {
        Some(path) => macos_open(&[std::ffi::OsStr::new("-a"), path.as_os_str(), std::ffi::OsStr::new(&link)]).await?,
        None => macos_open(&[std::ffi::OsStr::new(&link)]).await?,
    }
    Ok(app.is_some())
}

/// Ссылка на чат: в приложение, если оно есть, иначе на t.me в браузере.
fn telegram_link(address: &str, text: Option<&str>, app: bool) -> String {
    let query = text.map(url_encode).map(|t| format!("text={t}"));
    match (address.strip_prefix('@'), app) {
        (Some(nick), true) => format!("tg://resolve?domain={nick}{}", query.map_or(String::new(), |q| format!("&{q}"))),
        (None, true) => format!("tg://resolve?phone={}{}", address.trim_start_matches('+'), query.map_or(String::new(), |q| format!("&{q}"))),
        (Some(nick), false) => format!("https://t.me/{nick}{}", query.map_or(String::new(), |q| format!("?{q}"))),
        (None, false) => format!("https://t.me/{address}{}", query.map_or(String::new(), |q| format!("?{q}"))),
    }
}

fn human_time(secs: u64) -> String {
    let (h, m, s) = (secs / 3600, secs % 3600 / 60, secs % 60);
    let mut parts = Vec::new();
    if h > 0 { parts.push(format!("{h} год")); }
    if m > 0 { parts.push(format!("{m} хв")); }
    if s > 0 { parts.push(format!("{s} с")); }
    parts.join(" ")
}

/// Предел получателей одной команды: ошибка распознавания не должна разойтись по всем чатам.
const MAX_RECIPIENTS: usize = 5;

/// Нажимает Enter в открытом чате Forkgram. Запущенному клиенту нужна секунда на чат, только что запущенному больше.
async fn telegram_send(was_running: bool) -> Result<(), String> {
    tokio::time::sleep(Duration::from_millis(if was_running { 1200 } else { 6000 })).await;
    // Сначала проверяем, что впереди именно Forkgram: Enter в другом окне отправил бы текст куда попало.
    let script = "tell application \"Forkgram\" to activate\ndelay 0.5\ntell application \"System Events\"\nset front_app to name of first process whose frontmost is true\nif front_app does not contain \"Forkgram\" then error \"not frontmost\"\nkey code 36\nend tell";
    let out = tokio::time::timeout(Duration::from_secs(10), tokio::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).kill_on_drop(true).output())
        .await
        .map_err(|_| "Forkgram не відповідає, не надіслала".to_string())?
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        return Ok(());
    }
    let err = String::from_utf8_lossy(&out.stderr);
    Err(if err.contains("not frontmost") {
        "Forkgram не вийшов на передній план, не надіслала: текст лишився в полі".into()
    } else if ["1002", "-1719", "-25211", "-1743", "not allowed"].iter().any(|k| err.contains(k)) {
        "macOS не дала натиснути Enter, не надіслала: дозволь це в Налаштуваннях, Конфіденційність, Універсальний доступ".into()
    } else {
        // Не выдаём всякий сбой за нехватку прав: иначе ищешь причину не там.
        format!("не вдалося натиснути Enter, не надіслала: {}", crate::util::truncate_chars(err.trim(), 160))
    })
}

/// Текущий выход Mullvad или причина, почему VPN не считается включённым.
pub async fn vpn_status() -> Result<String, String> {
    let out = tokio::time::timeout(Duration::from_secs(10), tokio::process::Command::new(MULLVAD).args(["status", "-j"]).kill_on_drop(true).output())
        .await
        .map_err(|_| "Mullvad не відповідає".to_string())?
        .map_err(|_| "Mullvad VPN не знайдено".to_string())?;
    vpn_summary(&String::from_utf8_lossy(&out.stdout))
}

const MULLVAD: &str = "/usr/local/bin/mullvad";

/// Истина только при явном подтверждении выхода через Mullvad: любой другой ответ считаем отказом.
pub fn vpn_summary(status: &str) -> Result<String, String> {
    let v: Value = serde_json::from_str(status).map_err(|_| "незрозуміла відповідь Mullvad".to_string())?;
    let place = &v["details"]["location"];
    if v["state"] != "connected" || place["mullvad_exit_ip"] != true {
        return Err("Mullvad VPN не підключений".into());
    }
    Ok(format!("Mullvad VPN підключений: {}, {}", place["country"].as_str().unwrap_or("?"), place["city"].as_str().unwrap_or("?")))
}

/// Открыть адрес. В инкогнито только в Mullvad Browser: куки чистятся при закрытии и он не связан с обычным браузером.
async fn open_url(url: &str, incognito: bool) -> Result<(), String> {
    if !incognito {
        return macos_open(&[std::ffi::OsStr::new(url)]).await;
    }
    macos_open(&[std::ffi::OsStr::new("-a"), std::ffi::OsStr::new("Mullvad Browser"), std::ffi::OsStr::new(url)])
        .await
        .map_err(|_| "Mullvad Browser не відкрився".to_string())
}

/// Что нельзя делать в инкогнито: эти приложения привязаны к аккаунтам владельца.
fn tied_to_account(skill: &Skill) -> bool {
    matches!(skill, Skill::SpotifySearch(_) | Skill::TgDraft { .. } | Skill::TgCompose { .. } | Skill::ScreenLook | Skill::Call { .. } | Skill::DiscordVoice(_))
}

/// Голос приносит «Юно, будь ласка, відкрий мені ютуб.» и «можеш увімкнути музику»: приводим к команде.
fn command_text(q: &str) -> String {
    const LEADING: &[&str] = &["юна", "юно", "юню", "yuna", "будь ласка", "пожалуйста", "слухай", "слушай", "давай", "можеш", "можешь", "ну", "а", "ей", "эй", "мені", "мне"];
    const AFTER_VERB: &[&str] = &["будь ласка", "пожалуйста", "мені", "мне"];
    const INFINITIVES: &[(&str, &str)] = &[
        ("відкрити", "відкрий"), ("открыть", "открой"), ("запустити", "запусти"), ("запустить", "запусти"), ("увімкнути", "увімкни"), ("ввімкнути", "увімкни"),
        ("ввімкни", "увімкни"), ("включити", "включи"), ("включить", "включи"), ("вруби", "включи"), ("поставити", "постав"), ("поставь", "постав"), ("поставить", "постав"),
    ];
    fn strip_filler<'a>(t: &'a str, fillers: &[&str]) -> Option<&'a str> {
        let low = t.to_lowercase();
        let f = fillers.iter().find(|f| low.starts_with(**f) && low[f.len()..].chars().next().is_none_or(|c| !c.is_alphanumeric()))?;
        Some(t.get(f.len()..)?.trim_start_matches(|c: char| c.is_whitespace() || ",.!:—-".contains(c)))
    }
    let mut t = q.trim();
    while let Some(rest) = strip_filler(t, LEADING) {
        t = rest;
    }
    let (first, tail) = t.split_once(char::is_whitespace).unwrap_or((t, ""));
    let first_low = first.to_lowercase();
    // Распознавание речи путает окончания («включі», «увімкне», «відкрив»), поэтому сверяем по основе.
    const STEMS: &[(&str, &str)] = &[("включ", "включи"), ("вімкн", "увімкни"), ("вмикн", "увімкни"), ("увімк", "увімкни"), ("ввімк", "увімкни"), ("відкри", "відкрий"), ("запуст", "запусти")];
    let verb = INFINITIVES.iter().find(|(from, _)| *from == first_low).map(|(_, to)| *to)
        .or_else(|| STEMS.iter().find(|(stem, _)| first_low.starts_with(stem)).map(|(_, to)| *to))
        .unwrap_or(first);
    let mut tail = tail.trim_start();
    if PLAY_VERBS.contains(&verb.to_lowercase().as_str()) {
        while let Some(rest) = strip_filler(tail, AFTER_VERB) {
            tail = rest;
        }
    }
    let mut out = format!("{verb} {tail}");
    let low = out.to_lowercase();
    for ending in [" будь ласка", " пожалуйста"] {
        if let Some(cut) = low.trim_end_matches(|c: char| ".!? ,".contains(c)).strip_suffix(ending) {
            out.truncate(cut.trim_end_matches(|c: char| ", ".contains(c)).len());
            break;
        }
    }
    out.trim().to_string()
}

/// Секунды таймера: «5 хвилин», «1 годину 30 хвилин», «90 сек». Число без единицы считаем минутами.
fn timer_secs(low: &str) -> Option<u64> {
    let mut total = 0u64;
    let mut found = false;
    let mut chars = low.chars().peekable();
    while let Some(c) = chars.next() {
        if !c.is_ascii_digit() {
            continue;
        }
        let mut n = c.to_digit(10)? as u64;
        while let Some(d) = chars.peek().and_then(|c| c.to_digit(10)) {
            n = n.saturating_mul(10).saturating_add(d as u64);
            chars.next();
        }
        let unit: String = chars.clone().skip_while(|c| c.is_whitespace()).take_while(|c| c.is_alphabetic()).collect();
        let k = if unit.starts_with("сек") { 1 } else if unit.starts_with("год") || unit.starts_with("час") { 3600 } else { 60 };
        total = total.saturating_add(n.saturating_mul(k));
        found = true;
    }
    (found && (1..=86_400).contains(&total)).then_some(total)
}

/// Пауза только как просьба: «зроби паузу в роботі» музыку не трогает.
fn asks_pause(low: &str) -> bool {
    let t = low.trim().trim_end_matches(|c: char| ".!?".contains(c));
    t == "пауза" || t == "паузу" || (t.contains("паузу") && t.split_whitespace().next().is_some_and(|w| matches!(w, "постав" | "поставь" | "зроби" | "сделай" | "вруби" | "включи" | "увімкни") ) && (t.contains("на паузу") || t.contains("музик") || t.contains("музык") || t.contains("трек")))
}

fn stop_verb_first(low: &str) -> bool {
    const STOP: &[&str] = &["иключ", "ыключ", "вимк", "зупин", "останов", "вируб", "выруб", "заглуш", "стоп"];
    let words: Vec<&str> = low.split_whitespace().collect();
    match words.iter().take(3).position(|w| STOP.iter().any(|s| w.contains(s))) {
        Some(i) if i > 0 && words[i - 1] == "не" => false,
        Some(_) => true,
        None => false,
    }
}

const VIDEO_WORDS: &[&str] = &["відео", "видео", "відос", "видос", "ютуб", "youtube"];

fn asks_video_stop(low: &str) -> bool {
    // Пауза только как просьба: «вчора дивився відео про паузу» не команда.
    let first = low.split_whitespace().next().unwrap_or("");
    let wants_pause = low.contains("пауз") && (matches!(first, "постав" | "поставь" | "зроби" | "сделай" | "пауза") || VIDEO_WORDS.iter().any(|k| first.starts_with(k)));
    (stop_verb_first(low) || wants_pause) && VIDEO_WORDS.iter().any(|k| low.contains(k)) && !low.contains("не ")
}

fn asks_video_resume(low: &str) -> bool {
    let first = low.split_whitespace().next().unwrap_or("");
    (matches!(first, "продовж" | "продолжи" | "віднови") || low.contains("з паузи") || low.contains("с паузы")) && VIDEO_WORDS.iter().any(|k| low.contains(k))
}

/// «Вимкни музику», в том числе с опечаткой («іиключи»). «Включи» сюда не попадает: в нём нет «иключ».
fn asks_music_stop(low: &str) -> bool {
    const MUSIC: &[&str] = &["музик", "музык", "трек", "пісн", "песн", "споті", "споти", "spotify", "плейлист"];
    stop_verb_first(low) && MUSIC.iter().any(|m| low.contains(m))
}

const SAVE_VERBS: &[&str] = &["запам", "збережи", "сохрани", "запомни"];
const PLAY_VERBS: &[&str] = &["запусти", "увімкни", "включи", "постав", "відтвори", "відкрий", "открой", "покажи"];

/// Убирает служебные слова. `*` в конце: совпадение по началу слова, иначе только целиком,
/// чтобы «в» и «на» не съедали «вечірній» и «настрій».
fn strip_words(text: &str, words: &[&str]) -> String {
    text.split_whitespace().filter(|w| {
        let w = w.to_lowercase();
        let w = w.trim_matches(|c: char| ",.!?«»".contains(c));
        !words.iter().any(|x| match x.strip_suffix('*') {
            Some(stem) => w.starts_with(stem),
            None => w == *x,
        })
    }).collect::<Vec<_>>().join(" ")
}

/// Команды Spotify. Глагол обязателен в начале, поэтому разговор о музыке ничего не запускает.
fn parse_spotify(q: &str, low: &str) -> Option<Skill> {
    if let Some(uri) = first_url(q).as_deref().and_then(spotify_uri) {
        if SAVE_VERBS.iter().any(|k| low.starts_with(k)) {
            let name = strip_words(&q.split_whitespace().filter(|w| !w.contains("spotify.com")).collect::<Vec<_>>().join(" "), &["запам*", "збережи", "сохрани", "запомни*", "плейлист*", "мій", "мой", "це", "як"]);
            return Some(Skill::SpotifySave { name: (!name.is_empty()).then(|| name.to_lowercase()), uri });
        }
        if q.split_whitespace().count() == 1 {
            return Some(Skill::SpotifyLink(uri));
        }
        return PLAY_VERBS.iter().any(|v| low.starts_with(v)).then_some(Skill::SpotifyUri(uri));
    }
    if SAVE_VERBS.iter().any(|k| low.starts_with(k)) && low.contains("плейлист") {
        return Some(Skill::Explain("тут немає посилання на Spotify. Надішли посилання виду https://open.spotify.com/playlist/… разом із цією командою"));
    }
    let verb = PLAY_VERBS.iter().find(|v| low.starts_with(**v))?;
    let rest_low = low[verb.len()..].trim();
    let is_spotify = ["споті", "споти", "spotify"].iter().any(|k| rest_low.contains(k));
    if rest_low.contains("плейлист") {
        if !is_spotify && (rest_low.contains("ютуб") || rest_low.contains("youtube")) {
            return None;
        }
        let name = strip_words(rest_low, &["музик*", "музык*", "плейлист*", "якусь", "яку-небудь", "будь-яку", "щось", "мій", "мого", "мой", "моего", "моєму", "моем", "з", "із", "с", "у", "в", "на", "споті*", "споти*", "spotify*"]);
        return Some(Skill::SpotifyPlaylist((!name.is_empty()).then_some(name)));
    }
    let not_search = ["пауз", "програм", "застосун", "додаток", "приложени", "ютуб", "youtube"].iter().any(|k| rest_low.contains(k));
    if not_search || (!is_spotify && !rest_low.starts_with("музик") && !rest_low.starts_with("музык")) {
        return None;
    }
    let query = strip_words(&q[verb.len()..], &["музик*", "музык*", "у", "в", "на", "споті*", "споти*", "spotify*"]);
    if query.is_empty() {
        // «Відкрий спотіфай» открывает программу, играть просили только «музику».
        return (rest_low.contains("музик") || rest_low.contains("музык")).then_some(Skill::Spotify(SpotifyCmd::Play));
    }
    Some(Skill::SpotifySearch(crate::util::truncate_chars(&query, 120)))
}

/// «Запусти відео про космос», «відкрий якийсь відос на ютубі».
fn parse_video(low: &str) -> Option<Skill> {
    let verb = PLAY_VERBS.iter().find(|v| low.starts_with(**v))?;
    let rest = low[verb.len()..].trim().trim_end_matches(|c: char| "?!.".contains(c));
    // Целым словом: «відкрий відеоредактор» просит программу, а не ролик.
    let says_video = rest.split_whitespace().any(|w| matches!(w, "відео" | "видео") || w.starts_with("відос") || w.starts_with("видос"));
    // «Відкрий щось цікаве»: выбирает сама. «Увімкни щось послухати» оставляем музыке.
    let surprise = rest.split_whitespace().next().is_some_and(|w| matches!(w, "щось" | "что-нибудь")) && !rest.contains("послух");
    if surprise {
        return Some(Skill::PlayVideo(crate::util::truncate_chars(&strip_words(rest, &["щось", "что-нибудь", "цікаве", "интересное", "подивитись", "подивитися", "посмотреть"]), 120)));
    }
    // «Запусти на ютубі котів» просит включить, а «відкрий на ютубі котів» показать результаты.
    let plays_on_youtube = !matches!(*verb, "відкрий" | "открой" | "покажи") && ["ютуб", "youtube"].iter().any(|k| rest.contains(k)) && rest.split_whitespace().count() > 1;
    if !says_video && !plays_on_youtube {
        return None;
    }
    let query = strip_words(rest, &["якийсь", "якесь", "який", "яке", "будь", "будь-який", "будь-яке", "какой", "какое", "какой-нибудь", "відео", "видео", "відос*", "видос*", "мені", "мне", "на", "в", "у", "ютуб*", "youtube*"]);
    Some(Skill::PlayVideo(crate::util::truncate_chars(&query, 120)))
}

/// Что включить, если просят «якийсь відос» без темы.
const ANY_VIDEO: &[&str] = &["цікаві факти про космос", "документальний фільм природа", "lofi hip hop", "як працює техніка", "найкращі моменти подорожей", "смішні тварини"];

const PLAYLISTS_FILE: &str = "data/spotify.json";

/// Только URI плейлистов: ни логина, ни токена здесь нет.
#[derive(Default, serde::Serialize, serde::Deserialize)]
struct Playlists {
    default: Option<String>,
    #[serde(default)]
    named: std::collections::HashMap<String, String>,
}

fn load_playlists(path: &std::path::Path) -> Playlists {
    std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

/// Запоминает плейлист. Первый сохранённый становится основным, безымянный всегда заменяет основной.
pub fn save_playlist(path: &std::path::Path, name: Option<&str>, uri: &str) -> Result<(), String> {
    let mut all = load_playlists(path);
    match name {
        Some(n) => {
            all.named.insert(n.to_string(), uri.to_string());
            all.default.get_or_insert_with(|| uri.to_string());
        }
        None => all.default = Some(uri.to_string()),
    }
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::write(path, serde_json::to_vec_pretty(&all).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

/// URI нужного плейлиста или объяснение, что сделать.
pub fn find_playlist(path: &std::path::Path, name: Option<&str>) -> Result<String, String> {
    let all = load_playlists(path);
    let found = match name {
        Some(n) => all.named.get(n).cloned(),
        None => all.default.clone(),
    };
    found.ok_or_else(|| match name {
        Some(n) => format!("плейлиста «{n}» я не знаю. Скажи: запам'ятай плейлист {n} і встав посилання зі Spotify"),
        None => "я ще не знаю твій плейлист. У Spotify: Поділитися, Копіювати посилання, а мені скажи: запам'ятай плейлист і встав посилання".to_string(),
    })
}

async fn spotify_running() -> bool {
    tokio::process::Command::new("/usr/bin/pgrep").args(["-x", "Spotify"]).output().await.map(|o| o.status.success()).unwrap_or(false)
}

async fn command_text_of(program: &str, args: &[&str]) -> String {
    tokio::process::Command::new(program).args(args).output().await.map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default()
}

/// Только общие характеристики: ни имени пользователя, ни серийного номера.
async fn system_info() -> String {
    let cpu = command_text_of("/usr/sbin/sysctl", &["-n", "machdep.cpu.brand_string"]).await;
    let model = command_text_of("/usr/sbin/sysctl", &["-n", "hw.model"]).await;
    let bytes: u64 = command_text_of("/usr/sbin/sysctl", &["-n", "hw.memsize"]).await.parse().unwrap_or(0);
    let cores = command_text_of("/usr/sbin/sysctl", &["-n", "hw.ncpu"]).await;
    let macos = command_text_of("/usr/bin/sw_vers", &["-productVersion"]).await;
    let battery = command_text_of("/usr/bin/pmset", &["-g", "batt"]).await;
    let df = command_text_of("/bin/df", &["-k", "/System/Volumes/Data"]).await;
    format!("{}{}", pc_summary(&cpu, &model, &cores, bytes, &macos, &battery), disk_summary(&df))
}

pub fn pc_summary(cpu: &str, model: &str, cores: &str, memory_bytes: u64, macos: &str, battery: &str) -> String {
    let charge = battery.split_whitespace().find(|w| w.contains('%')).map(|w| w.trim_end_matches(';').to_string());
    format!("Модель {model}, процесор {cpu}, ядер {cores}, оперативка {} ГБ, macOS {macos}{}", memory_bytes / (1 << 30), charge.map_or(String::new(), |c| format!(", заряд {c}")))
}

fn disk_summary(df: &str) -> String {
    let free = df.lines().nth(1).and_then(|l| l.split_whitespace().nth(3)).and_then(|k| k.parse::<u64>().ok());
    free.map_or(String::new(), |k| format!(", вільно на диску {} ГБ", k / (1 << 20)))
}

/// Идёт ли сейчас звук на выход: macOS держит для этого отметку `audio-out`.
async fn audio_playing() -> bool {
    tokio::process::Command::new("/usr/bin/pmset").args(["-g", "assertions"]).output().await.is_ok_and(|o| String::from_utf8_lossy(&o.stdout).contains("audio-out"))
}

/// Нажимает K в окне браузера, где активна вкладка YouTube. Заголовок окна показывает только активную вкладку.
/// Клавиша физическая (`key code`): при украинской раскладке буква «k» не набралась бы.
async fn press_youtube_key() -> Result<bool, String> {
    // Поиск отдельно от нажатия: `try` вокруг всего глотал сбой AXRaise, и клавиша не нажималась.
    let script = "tell application \"System Events\"\nset target to missing value\nset found to missing value\nrepeat with p in (every process whose visible is true)\ntry\nrepeat with w in windows of p\nif name of w contains \"YouTube\" then\nset target to name of p\nset found to w\nexit repeat\nend if\nend repeat\nend try\nif target is not missing value then exit repeat\nend repeat\nif target is missing value then return \"none\"\nset frontmost of process target to true\ndelay 0.3\ntry\nperform action \"AXRaise\" of found\nend try\ndelay 0.1\nkey code 40\nreturn \"pressed\"\nend tell";
    let out = tokio::time::timeout(Duration::from_secs(10), tokio::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).kill_on_drop(true).output())
        .await
        .map_err(|_| "браузер не відповідає".to_string())?
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err("macOS не дала натиснути клавішу: перевір Універсальний доступ".into());
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim() == "pressed")
}

/// K на YouTube переключает, поэтому смотрим на звук до и после: иначе «пауза» запустила бы остановленное видео.
async fn video_control(play: bool) -> Result<String, String> {
    let before = audio_playing().await;
    if !play && !before {
        return Ok("Зараз нічого не грає".into());
    }
    if play && before {
        return Ok("Вже щось грає".into());
    }
    if !press_youtube_key().await? {
        return Err("не бачу YouTube: вкладка з відео має бути активною у вікні браузера".into());
    }
    tokio::time::sleep(Duration::from_millis(1500)).await;
    Ok(match (play, audio_playing().await) {
        (false, false) => "YouTube на паузі".to_string(),
        (false, true) => "Натиснула паузу, але звук ще йде".to_string(),
        (true, true) => "YouTube грає".to_string(),
        (true, false) => "Натиснула запуск, але звуку поки немає".to_string(),
    })
}

/// «Вимкни музику» про то, что реально играет: Spotify, а если он молчит, YouTube.
async fn pause_media() -> Result<String, String> {
    if spotify_running().await {
        let state = tokio::process::Command::new("/usr/bin/osascript").args(["-e", "tell application \"Spotify\" to player state as text"]).output().await;
        if state.is_ok_and(|o| String::from_utf8_lossy(&o.stdout).trim() == "playing") {
            return spotify_pause().await;
        }
    }
    video_control(false).await
}

/// Ставит на паузу и проверяет: состояние плеера обновляется с задержкой.
async fn spotify_pause() -> Result<String, String> {
    if !spotify_running().await {
        return Ok("Spotify не запущений, музика з нього не грає".into());
    }
    let script = "tell application \"Spotify\"\npause\nrepeat 10 times\nif player state is not playing then exit repeat\ndelay 0.3\nend repeat\nreturn player state as text\nend tell";
    let out = tokio::time::timeout(Duration::from_secs(10), tokio::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).kill_on_drop(true).output())
        .await
        .map_err(|_| "Spotify не відповідає".to_string())?
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err("Spotify недоступний або не дозволено керування".into());
    }
    if String::from_utf8_lossy(&out.stdout).trim() == "playing" {
        return Err("Spotify далі грає, команду паузи він проігнорував".into());
    }
    Ok("Spotify на паузі".into())
}

/// Запускает Spotify при необходимости и выполняет фиксированный сценарий. URI идёт аргументом, а не в текст сценария.
async fn spotify_play(uri: Option<&str>) -> Result<String, String> {
    if let Some(u) = uri {
        if !valid_spotify_uri(u) {
            return Err("це посилання на Spotify я не запускаю".into());
        }
    }
    if !spotify_running().await {
        macos_open(&[std::ffi::OsStr::new("-g"), std::ffi::OsStr::new("-a"), std::ffi::OsStr::new("Spotify")]).await.map_err(|_| "Spotify не встановлений або не запускається".to_string())?;
        tokio::time::sleep(Duration::from_secs(4)).await;
    }
    let play = if uri.is_some() { "play track (item 1 of argv)" } else { "play" };
    let script = format!("on run argv\ntell application \"Spotify\"\n{play}\ndelay 1\nreturn (player state as text) & \"\t\" & (name of current track) & \"\t\" & (artist of current track)\nend tell\nend run");
    let mut cmd = tokio::process::Command::new("/usr/bin/osascript");
    cmd.arg("-e").arg(script).kill_on_drop(true);
    if let Some(u) = uri {
        cmd.arg(u);
    }
    let out = tokio::time::timeout(Duration::from_secs(15), cmd.output())
        .await
        .map_err(|_| "Spotify не відповідає".to_string())?
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(if String::from_utf8_lossy(&out.stderr).contains("-1743") {
            "macOS не дозволила керувати Spotify: дозволь це в Налаштуваннях, Конфіденційність, Автоматизація".into()
        } else {
            "Spotify не виконав команду: перевір, чи виконано вхід у застосунку".into()
        });
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut f = text.trim().split('\t');
    let (state, title, artist) = (f.next().unwrap_or(""), f.next().unwrap_or(""), f.next().unwrap_or(""));
    if state == "playing" {
        Ok(format!("Spotify грає: {title} — {artist}"))
    } else {
        Err("Spotify не почав грати: у ньому нічого не вибрано. Дай мені посилання на плейлист".into())
    }
}

/// Только обычные веб-адреса: без file:, javascript:, логинов в адресе и управляющих символов.
pub fn safe_url(url: &str) -> bool {
    let rest = url.strip_prefix("https://").or_else(|| url.strip_prefix("http://"));
    let Some(rest) = rest else { return false };
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    url.len() <= 2000 && !url.chars().any(|c| c.is_control() || c.is_whitespace()) && !host.contains('@') && valid_domain(host.split(':').next().unwrap_or(""))
}

fn url_encode(s: &str) -> String {
    s.bytes().map(|b| match b {
        b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
        _ => format!("%{b:02X}"),
    }).collect()
}

/// «Открой …»: сайт, поиск на YouTube или программа. Только если просьба начинается с этого глагола,
/// чтобы слово «відкрий» в середине обычной фразы ничего не запускало.
fn parse_open(q: &str, low: &str) -> Option<Skill> {
    const VERBS: &[&str] = &["відкрий", "открой", "запусти", "увімкни", "включи"];
    let verb = VERBS.iter().find(|v| low.starts_with(**v))?;
    let rest = q[verb.len()..].trim().trim_end_matches(|c: char| "?!.".contains(c)).trim();
    let rest_low = rest.to_lowercase();
    if rest.is_empty() || rest_low.contains("музик") {
        return None;
    }
    if let Some(url) = first_url(rest) {
        return safe_url(&url).then_some(Skill::OpenUrl(url));
    }
    const SITES: &[(&str, &str)] = &[
        ("ютуб", "https://www.youtube.com"), ("youtube", "https://www.youtube.com"), ("ютюб", "https://www.youtube.com"), ("утуб", "https://www.youtube.com"), ("у топ", "https://www.youtube.com"),
        ("у тоб", "https://www.youtube.com"), ("у туб", "https://www.youtube.com"), ("ю туб", "https://www.youtube.com"), ("ютуба", "https://www.youtube.com"), ("ютубі", "https://www.youtube.com"), ("гугл", "https://www.google.com"),
        ("google", "https://www.google.com"), ("гітхаб", "https://github.com"), ("github", "https://github.com"),
        ("ґітхаб", "https://github.com"), ("гмейл", "https://mail.google.com"), ("пошту", "https://mail.google.com"),
    ];
    for prefix in ["на ютубі ", "на youtube ", "в ютубі ", "ютуб ", "youtube ", "ютубі "] {
        if let Some(t) = rest_low.strip_prefix(prefix) {
            return Some(Skill::OpenUrl(format!("https://www.youtube.com/results?search_query={}", url_encode(t.trim()))));
        }
    }
    if let Some((_, url)) = SITES.iter().find(|(name, _)| *name == rest_low) {
        return Some(Skill::OpenUrl(url.to_string()));
    }
    let site = rest_low.strip_prefix("сайт ").unwrap_or(&rest_low).trim();
    if valid_domain(site) {
        return Some(Skill::OpenUrl(format!("https://{site}")));
    }
    let name = rest.strip_prefix("програму ").or_else(|| rest.strip_prefix("программу ")).or_else(|| rest.strip_prefix("застосунок ")).unwrap_or(rest);
    (name.chars().count() <= 40 && name.split_whitespace().count() <= 4).then(|| Skill::OpenApp(name.to_string()))
}

/// Программы, которые не открываем: через них легко выполнить команды или добраться до паролей.
const DENIED_APPS: &[&str] = &["terminal", "iterm", "warp", "script editor", "automator", "keychain access", "passwords", "console", "activity monitor", "disk utility", "migration assistant"];

/// Частые названия по-украински и по-русски.
fn app_alias(name: &str) -> String {
    const ALIASES: &[(&str, &str)] = &[
        ("телеграм", "forkgram"), ("telegram", "forkgram"), ("тг", "forkgram"), ("форкграм", "forkgram"), ("дискорд", "discord"), ("спотіфай", "spotify"), ("спотифай", "spotify"), ("хром", "google chrome"), ("гугл хром", "google chrome"), ("chrome", "google chrome"),
        ("фаєрфокс", "firefox"), ("файрфокс", "firefox"), ("сафарі", "safari"), ("сафари", "safari"), ("вс код", "visual studio code"), ("vs code", "visual studio code"),
        ("калькулятор", "calculator"), ("нотатки", "notes"), ("заметки", "notes"), ("календар", "calendar"), ("календарь", "calendar"), ("пошта", "mail"), ("почта", "mail"),
        ("фото", "photos"), ("музика", "music"), ("фіндер", "finder"), ("термінал", "terminal"), ("терминал", "terminal"), ("налаштування", "system settings"), ("настройки", "system settings"),
    ];
    let low = name.trim().to_lowercase();
    ALIASES.iter().find(|(a, _)| *a == low).map(|(_, e)| e.to_string()).unwrap_or(low)
}

/// Ищет программу по точному имени среди установленных приложений. Пути от пользователя не принимаем.
pub fn resolve_app(name: &str, dirs: &[std::path::PathBuf]) -> Result<std::path::PathBuf, String> {
    let wanted = app_alias(name);
    if DENIED_APPS.contains(&wanted.as_str()) {
        return Err(format!("{name}: цю програму я не відкриваю"));
    }
    for dir in dirs {
        let Ok(entries) = std::fs::read_dir(dir) else { continue };
        for e in entries.flatten() {
            let file = e.file_name().to_string_lossy().to_lowercase();
            if file.strip_suffix(".app") == Some(wanted.as_str()) {
                return Ok(e.path());
            }
        }
    }
    Err(format!("не знайшла програму «{name}»"))
}

fn app_dirs() -> Vec<std::path::PathBuf> {
    let home = std::env::var("HOME").unwrap_or_default();
    ["/Applications".to_string(), "/Applications/Utilities".to_string(), "/System/Applications".to_string(), "/System/Applications/Utilities".to_string(), "/System/Library/CoreServices".to_string(), format!("{home}/Applications")]
        .into_iter().map(std::path::PathBuf::from).collect()
}

/// Команда `open` без shell. Аргумент уже проверен, поэтому стать флагом не может.
async fn macos_open(args: &[&std::ffi::OsStr]) -> Result<(), String> {
    let out = tokio::time::timeout(Duration::from_secs(10), tokio::process::Command::new("/usr/bin/open").args(args).kill_on_drop(true).output())
        .await
        .map_err(|_| "відкриття надто довге".to_string())?
        .map_err(|e| e.to_string())?;
    if out.status.success() { Ok(()) } else { Err("macOS не змогла це відкрити".into()) }
}

/// Разбирает запрос владельца. Обычная болтовня навыком не считается.
pub fn parse(q: &str) -> Option<Skill> {
    let normalized = command_text(q);
    let q = normalized.as_str();
    let low = q.to_lowercase();
    if let Some(skill) = parse_comms(q, &low).or_else(|| parse_vpn(&low)).or_else(|| parse_convenience(q, &low)) {
        return Some(skill);
    }
    // Управление музыкой и таймер раньше «запусти …», иначе «запусти таймер» ищет такую программу.
    // Сначала продолжение: в «зніми ютуб з паузи» тоже есть слово «пауза».
    if asks_video_resume(&low) {
        return Some(Skill::Video { play: true });
    }
    if asks_video_stop(&low) {
        return Some(Skill::Video { play: false });
    }
    if asks_pause(&low) || asks_music_stop(&low) {
        return Some(Skill::Spotify(SpotifyCmd::Pause));
    }
    if ["наступний трек", "наступну пісню", "следующий трек", "следующую песню", "далі трек", "скіпни", "пропусти трек", "пропусти пісню"].iter().any(|k| low.contains(k)) {
        return Some(Skill::Spotify(SpotifyCmd::Next));
    }
    if ["попередній трек", "попередню пісню", "предыдущий трек", "предыдущую песню"].iter().any(|k| low.contains(k)) {
        return Some(Skill::Spotify(SpotifyCmd::Previous));
    }
    if ["що грає", "что играет", "який трек", "какой трек"].iter().any(|k| low.contains(k)) {
        return Some(Skill::Spotify(SpotifyCmd::Current));
    }
    if low.contains("таймер") {
        if let Some(secs) = timer_secs(&low) {
            return Some(Skill::Timer(secs));
        }
    }
    if let Some(skill) = parse_spotify(q, &low).or_else(|| parse_video(&low)) {
        return Some(skill);
    }
    if let Some(skill) = parse_open(q, &low) {
        return Some(skill);
    }
    if let Some(skill) = parse_arsenal(q, &low) {
        return Some(skill);
    }
    if ["увімкни музику", "включи музику", "включи музыку", "продовж музику", "продолжи музыку"].iter().any(|k| low.contains(k)) {
        return Some(Skill::Spotify(SpotifyCmd::Play));
    }
    if let Some(rest) = after(q, &low, &["індикатори:", "индикаторы:", "витягни індикатори", "витягни ioc", "ioc:"]) {
        if !rest.is_empty() {
            return Some(Skill::Indicators(rest.to_string()));
        }
    }
    for w in q.split_whitespace() {
        let w = w.trim_matches(|c: char| ",.;:!?«»\"'()".contains(c));
        if let Ok(ip) = w.parse::<IpAddr>() {
            if low.contains("ip") || low.contains("айпі") || low.contains("перевір") || low.contains("провер") || low.contains("пробий") {
                return public_ip(&ip).then(|| arsenal("ip", ip.to_string())).flatten().or(Some(Skill::IpInfo(ip)));
            }
        }
    }
    if ["dns", "rdap"].iter().any(|k| low.contains(k)) {
        if let Some(d) = q.split_whitespace().map(|w| w.trim_matches(|c: char| ",;:!?«»\"'()".contains(c)).trim_end_matches('.').to_lowercase()).find(|w| valid_domain(w)) {
            return Some(Skill::Domain(d));
        }
    }
    if let Some(city) = after(q, &low, &["погода в ", "погода у ", "погоду в ", "погоду у ", "погоди в ", "погоди у ", "погода ", "погоду "]) {
        // Навык знает только текущую погоду, а «сьогодні» не город.
        let city = strip_words(city, &["сьогодні", "сегодня", "зараз", "сейчас", "завтра", "в", "у", "там", "яка", "какая"]);
        let city = city.trim_end_matches(|c: char| "?!.,".contains(c)).trim();
        if !city.is_empty() && city.chars().count() <= 60 {
            return Some(Skill::Weather(city.to_string()));
        }
    }
    // Общий веб-поиск. Запросы про конкретных людей сюда не идут: досье не собираем.
    let nickname = low.split(|c: char| !c.is_alphanumeric()).any(|w| matches!(w, "нік" | "ник" | "ніка" | "ника" | "ніку" | "нику" | "нікнейм" | "никнейм"));
    let about_person = nickname || ["людин", "человек", "адрес", "телефон", "пошт", "почт", "акаунт", "аккаунт"].iter().any(|k| low.contains(k));
    if let Some(s) = after(q, &low, &["знайди в інтернеті", "найди в интернете", "пошукай", "погугли", "загугли", "знайди", "найди"]).filter(|_| !about_person) {
        let s = s.trim_end_matches(|c: char| "?!.".contains(c)).trim();
        if !s.is_empty() {
            return Some(Skill::Search(crate::util::truncate_chars(s, 120)));
        }
    }
    None
}

/// Индикаторы компрометации из текста, локально: IP, URL, почта, хеши.
pub fn extract_indicators(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for raw in text.split_whitespace() {
        let w = raw.trim_matches(|c: char| ",;:!?«»\"'()[]<>".contains(c)).trim_end_matches('.');
        let hex = w.len() >= 32 && w.chars().all(|c| c.is_ascii_hexdigit());
        let kind = if w.parse::<IpAddr>().is_ok() {
            Some("IP")
        } else if w.starts_with("http://") || w.starts_with("https://") {
            Some("URL")
        } else if hex && matches!(w.len(), 32 | 40 | 64) {
            Some(match w.len() { 32 => "MD5", 40 => "SHA-1", _ => "SHA-256" })
        } else if w.contains('@') && w.split('@').nth(1).is_some_and(valid_domain) {
            Some("email")
        } else if valid_domain(w) {
            Some("домен")
        } else {
            None
        };
        if let Some(k) = kind {
            let line = format!("{k}: {w}");
            if !out.contains(&line) {
                out.push(line);
            }
        }
        if out.len() >= 30 {
            break;
        }
    }
    out
}

fn html_text(fragment: &str) -> String {
    let mut out = String::new();
    let mut in_tag = false;
    for c in fragment.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            c if !in_tag => out.push(c),
            _ => {}
        }
    }
    out.replace("&amp;", "&").replace("&quot;", "\"").replace("&#x27;", "'").replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").trim().to_string()
}

/// Результаты со страницы DuckDuckGo: заголовок и описание. Разбор без зависимостей, текст дальше идёт в модель как данные.
pub fn search_results(html: &str, limit: usize) -> Vec<String> {
    html.split("class=\"result__a\"").skip(1).take(limit).filter_map(|block| {
        let start = block.find('>')? + 1;
        let end = start + block[start..].find("</a>")?;
        let title = crate::util::truncate_chars(&html_text(&block[start..end]), 140);
        let snippet = block.split("class=\"result__snippet\"").nth(1).and_then(|b| {
            let a = b.find('>')? + 1;
            let z = a + b[a..].find("</a>")?;
            Some(crate::util::truncate_chars(&html_text(&b[a..z]), 200))
        });
        Some(match snippet {
            Some(sn) if !sn.is_empty() => format!("{title}: {sn}"),
            _ => title,
        })
    }).collect()
}

/// Текст вместо кода WMO: сырое число модель пересказывает как попало.
fn weather_text(code: i64) -> &'static str {
    match code {
        0 => "ясно",
        1 | 2 => "мінлива хмарність",
        3 => "похмуро",
        45 | 48 => "туман",
        51..=57 => "мряка",
        61..=67 | 80..=82 => "дощ",
        71..=77 | 85 | 86 => "сніг",
        95..=99 => "гроза",
        _ => "без опису",
    }
}

pub fn weather_summary(place: &str, v: &Value) -> Option<String> {
    let c = &v["current"];
    Some(format!("{place} зараз: {}°C, {}, вітер {} км/год", c["temperature_2m"].as_f64()?, weather_text(c["weather_code"].as_i64()?), c["wind_speed_10m"].as_f64()?))
}

/// Геокодер не понимает падежей: «у Києві» → «Київ». Сначала таблица, затем форма без окончания.
pub fn city_candidates(city: &str) -> Vec<String> {
    const FORMS: &[(&str, &str)] = &[
        ("києві", "Київ"), ("киеве", "Киев"), ("львові", "Львів"), ("львове", "Львов"), ("харкові", "Харків"), ("харькове", "Харьков"),
        ("одесі", "Одеса"), ("одессе", "Одесса"), ("дніпрі", "Дніпро"), ("днепре", "Днепр"), ("полтаві", "Полтава"), ("вінниці", "Вінниця"),
        ("тернополі", "Тернопіль"), ("луцьку", "Луцьк"), ("рівному", "Рівне"), ("запоріжжі", "Запоріжжя"), ("чернігові", "Чернігів"),
    ];
    let low = city.to_lowercase();
    let mut out = Vec::new();
    if let Some((_, base)) = FORMS.iter().find(|(f, _)| *f == low) {
        out.push(base.to_string());
    }
    out.push(city.to_string());
    let chars: Vec<char> = city.chars().collect();
    if chars.len() > 4 && matches!(chars[chars.len() - 1], 'і' | 'е' | 'у' | 'и' | 'ї') {
        out.push(chars[..chars.len() - 1].iter().collect());
    }
    out
}

fn client() -> reqwest::Client {
    reqwest::Client::builder().timeout(Duration::from_secs(10)).user_agent("yuna-cohost/0.1").redirect(reqwest::redirect::Policy::limited(2)).build().expect("http")
}

async fn get_json(url: &str, query: &[(&str, &str)]) -> Result<Value, String> {
    client().get(url).query(query).send().await.map_err(|e| e.without_url().to_string())?.json().await.map_err(|e| e.without_url().to_string())
}

/// Навык без сети: в инкогнито ему ничего не грозит.
/// Навык управляет компьютером, а не только ищет в сети. Без `PC_CONTROL=true` не запускается.
pub fn touches_pc(skill: &Skill) -> bool {
    !matches!(
        skill,
        Skill::Weather(_) | Skill::Timer(_) | Skill::Search(_) | Skill::IpInfo(_) | Skill::Domain(_) | Skill::Indicators(_) | Skill::Arsenal { .. } | Skill::Explain(_) | Skill::Help
    )
}

pub fn is_local(skill: &Skill) -> bool {
    matches!(
        skill,
        Skill::Timer(_) | Skill::Indicators(_) | Skill::Spotify(_) | Skill::OpenApp(_) | Skill::SpotifyUri(_) | Skill::SpotifyLink(_) | Skill::SpotifyPlaylist(_) | Skill::SpotifySave { .. } | Skill::Explain(_) | Skill::Vpn { .. } | Skill::SaveContact { .. } | Skill::SaveVoice { .. } | Skill::Remind { .. } | Skill::Volume(_) | Skill::QuitApp(_) | Skill::Help | Skill::Video { .. } | Skill::SysInfo
    )
}

fn arsenal_python() -> String {
    std::env::var("ARSENAL_PYTHON")
        .unwrap_or_else(|_| format!("{}/osint-kit/.venv/bin/python", std::env::var("HOME").unwrap_or_default()))
}

/// Вызов `tools/arsenal.py`. Аргументы идут списком, без shell.
async fn run_arsenal(tool: &str, target: &str, incognito: bool) -> Result<String, String> {
    let python = arsenal_python();
    let mode = if incognito { "incognito" } else { "free" };
    let run = tokio::process::Command::new(python)
        .args(["-I", "tools/arsenal.py", mode, tool, target])
        .kill_on_drop(true)
        .output();
    let out = tokio::time::timeout(Duration::from_secs(150), run)
        .await
        .map_err(|_| "інструмент не відповів вчасно".to_string())?
        .map_err(|e| format!("не запускається osint-kit: {e}"))?;
    let v: Value = serde_json::from_slice(&out.stdout).map_err(|_| "незрозуміла відповідь інструмента".to_string())?;
    let text = v["text"].as_str().unwrap_or("").to_string();
    if v["ok"].as_bool().unwrap_or(false) {
        Ok(format!("{}\n{text}", v["title"].as_str().unwrap_or("")))
    } else {
        Err(text)
    }
}

/// Выполняет навык и возвращает результат для модели. Таймер обрабатывается отдельно в pipeline.
/// В инкогнито сетевые навыки без подтверждённого Mullvad VPN отказывают, а не идут напрямую.
pub async fn run(skill: &Skill, incognito: bool) -> Result<String, String> {
    if incognito && tied_to_account(skill) {
        return Err("в інкогніто недоступно: це прив'язано до твоїх акаунтів. Перемкнись на Вільний".into());
    }
    // Без подтверждённого туннеля инкогнито не должно молча стать прямым подключением.
    if incognito && !is_local(skill) {
        vpn_status().await.map_err(|e| format!("{e}, запит не надіслано"))?;
    }
    match skill {
        Skill::Vpn { connect } => {
            if *connect {
                let run = tokio::process::Command::new(MULLVAD).args(["connect", "--wait"]).kill_on_drop(true).output();
                tokio::time::timeout(Duration::from_secs(30), run).await.map_err(|_| "Mullvad не підключився за 30 секунд".to_string())?.map_err(|_| "Mullvad VPN не знайдено".to_string())?;
            }
            vpn_status().await
        }
        Skill::SaveContact { name, address } => {
            let path = std::path::Path::new(BOOK_FILE);
            let mut book = load_book(path);
            let entry = book.contacts.entry(name.clone()).or_default();
            // У контакта один ник и один номер: новый заменяет старый того же вида.
            entry.retain(|a| a.starts_with('@') != address.starts_with('@'));
            entry.push(address.clone());
            save_book(path, &book)?;
            Ok(format!("Запам'ятала контакт «{name}»"))
        }
        Skill::SaveVoice { name, link } => {
            let path = std::path::Path::new(BOOK_FILE);
            let mut book = load_book(path);
            book.voices.insert(name.clone(), link.clone());
            save_book(path, &book)?;
            Ok(format!("Запам'ятала войс «{name}»"))
        }
        Skill::Help => Ok(HELP_TEXT.to_string()),
        Skill::Volume(cmd) => volume(*cmd).await,
        Skill::QuitApp(name) => quit_app(name).await,
        Skill::Remind { secs, text } => Ok(format!("Нагадаю через {}: {text}", human_time(*secs))),
        // Снимок делает конвейер: сюда это доходит только в инкогнито, а там оно уже отклонено.
        Skill::ScreenLook => Err("екран зараз не показую".into()),
        // Конвейер заменяет это на `TgDraft` с готовым текстом.
        Skill::TgCompose { .. } => Err("текст ще не придуманий".into()),
        Skill::TgDraft { to, text } => {
            // Сначала находим всех: при неизвестном имени не должно уйти никому.
            let book = load_book(std::path::Path::new(BOOK_FILE));
            let mut addresses: Vec<String> = Vec::new();
            for name in to.split_whitespace() {
                let found = find_contact(&book, name)?;
                let Some(address) = found.iter().find(|a| a.starts_with('@')).or(found.first()) else {
                    return Err(format!("для «{name}» немає ні ніка, ні номера"));
                };
                if !addresses.contains(address) {
                    addresses.push(address.clone());
                }
            }
            if addresses.len() > MAX_RECIPIENTS {
                return Err(format!("за раз надсилаю не більше ніж {MAX_RECIPIENTS} людям, нічого не надіслала"));
            }
            let mut running = tokio::process::Command::new("/usr/bin/pgrep").args(["-x", "Forkgram"]).output().await.map(|o| o.status.success()).unwrap_or(false);
            let mut sent: Vec<&str> = Vec::new();
            for address in &addresses {
                let done = if sent.is_empty() { String::from("нікому не надіслала") } else { format!("вже надіслала: {}", sent.join(", ")) };
                if !open_chat(address, Some(text)).await.map_err(|e| format!("{address}: {e}; {done}"))? {
                    return Ok(format!("Forkgram не встановлений, відкрила чат {address} у браузері: «{text}». Надіслати там доведеться самому"));
                }
                telegram_send(running).await.map_err(|e| format!("{address}: {e}; {done}"))?;
                running = true;
                sent.push(address);
            }
            // Доставку отсюда не видно, поэтому результат назван точно: нажат Enter.
            Ok(format!("Натиснула надіслати {}: {text}", sent.join(", ")))
        }
        Skill::Call { to, telegram } => {
            let book = load_book(std::path::Path::new(BOOK_FILE));
            let found = find_contact(&book, to)?;
            let phone = found.iter().find(|a| a.starts_with('+'));
            let nick = found.iter().find(|a| a.starts_with('@'));
            if let (Some(p), false) = (phone, *telegram) {
                macos_open(&[std::ffi::OsStr::new(&format!("tel:{p}"))]).await.map_err(|_| "macOS не змогла почати дзвінок".to_string())?;
                return Ok(format!("Натисни «Подзвонити» для {to}"));
            }
            let Some(address) = nick.or(phone) else {
                return Err(format!("для «{to}» немає контакту для дзвінка"));
            };
            open_chat(address, None).await?;
            Ok(format!("Відкрила чат {to}, натисни трубку сам"))
        }
        Skill::DiscordVoice(target) => {
            let path = if target.contains('/') { target.clone() } else {
                let book = load_book(std::path::Path::new(BOOK_FILE));
                book.voices.get(target).cloned().ok_or_else(|| format!("войс «{target}» я не знаю. Скажи: запам'ятай войс {target} і встав посилання на канал"))?
            };
            macos_open(&[std::ffi::OsStr::new(&format!("discord://-/channels/{path}"))]).await.map_err(|_| "Discord не відкрився".to_string())?;
            Ok("Відкрила канал, натисни Join Voice".into())
        }
        Skill::Explain(text) => Err(text.to_string()),
        Skill::Arsenal { tool, target } => run_arsenal(tool, target, incognito).await,
        Skill::PlayVideo(query) => {
            let chosen;
            let target = if query.is_empty() {
                chosen = ANY_VIDEO[(crate::util::now_ms() as usize) % ANY_VIDEO.len()];
                chosen
            } else {
                query.as_str()
            };
            let found = run_arsenal("youtube_first", target, incognito).await?;
            let line = found.split_once('\n').map(|(_, rest)| rest).unwrap_or("");
            let mut f = line.split('\t');
            let (id, title) = (f.next().unwrap_or(""), f.next().unwrap_or(""));
            if id.len() != 11 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
                return Err("не знайшла відео".into());
            }
            let url = format!("https://www.youtube.com/watch?v={id}&autoplay=1");
            open_url(&url, incognito).await?;
            // Браузер часто блокирует автозапуск: страница открыта, а звука нет. Тогда нажимаем сами и говорим как есть.
            tokio::time::sleep(Duration::from_secs(6)).await;
            if !audio_playing().await && press_youtube_key().await.unwrap_or(false) {
                tokio::time::sleep(Duration::from_millis(1500)).await;
            }
            Ok(if audio_playing().await {
                format!("Запустила: {title}")
            } else {
                format!("Відкрила {title}, але не грає: натисни запуск")
            })
        }
        Skill::SpotifyUri(uri) => spotify_play(Some(uri)).await,
        Skill::SpotifyLink(uri) => {
            let path = std::path::Path::new(PLAYLISTS_FILE);
            if uri.starts_with("spotify:playlist:") && find_playlist(path, None).is_err() {
                save_playlist(path, None, uri)?;
            }
            spotify_play(Some(uri)).await
        }
        Skill::SpotifyPlaylist(name) => {
            let uri = find_playlist(std::path::Path::new(PLAYLISTS_FILE), name.as_deref())?;
            spotify_play(Some(&uri)).await
        }
        Skill::SpotifySave { name, uri } => {
            save_playlist(std::path::Path::new(PLAYLISTS_FILE), name.as_deref(), uri)?;
            Ok(match name {
                Some(n) => format!("Запам'ятала плейлист «{n}»"),
                None => "Запам'ятала твій основний плейлист".into(),
            })
        }
        Skill::SpotifySearch(q) => {
            let uri = format!("spotify:search:{}", url_encode(q));
            macos_open(&[std::ffi::OsStr::new(&uri)]).await.map_err(|_| "Spotify не встановлений".to_string())?;
            Ok(format!("Відкрила пошук «{q}» у Spotify, трек обери сам"))
        }
        Skill::OpenUrl(url) => {
            if !safe_url(url) {
                return Err("це посилання я не відкриваю".into());
            }
            open_url(url, incognito).await?;
            // Адрес целиком незачем читать вслух.
            let host = url.split("//").nth(1).and_then(|r| r.split('/').next()).unwrap_or(url).trim_start_matches("www.");
            Ok(format!("Відкрила {host}"))
        }
        Skill::OpenApp(name) => {
            let app = resolve_app(name, &app_dirs())?;
            macos_open(&[std::ffi::OsStr::new("-a"), app.as_os_str()]).await?;
            Ok(format!("Відкрила {}", app.file_stem().unwrap_or_default().to_string_lossy()))
        }
        Skill::Timer(s) => Ok(format!("Таймер на {}", human_time(*s))),
        Skill::Indicators(t) => {
            let found = extract_indicators(t);
            Ok(if found.is_empty() { "індикаторів не знайдено".into() } else { found.join("; ") })
        }
        Skill::Weather(city) => {
            let mut geo = Value::Null;
            for name in city_candidates(city) {
                geo = get_json("https://geocoding-api.open-meteo.com/v1/search", &[("name", name.as_str()), ("count", "1"), ("language", "uk")]).await?;
                if geo["results"][0]["latitude"].is_number() {
                    break;
                }
            }
            let p = &geo["results"][0];
            let (lat, lon) = (p["latitude"].as_f64().ok_or("місто не знайдено")?, p["longitude"].as_f64().ok_or("місто не знайдено")?);
            let (lat, lon) = (lat.to_string(), lon.to_string());
            let w = get_json("https://api.open-meteo.com/v1/forecast", &[("latitude", &lat), ("longitude", &lon), ("current", "temperature_2m,wind_speed_10m,weather_code")]).await?;
            weather_summary(p["name"].as_str().unwrap_or(city), &w).ok_or_else(|| "немає даних".to_string())
        }
        Skill::Search(q) => {
            // Bing на украинских запросах отдавал случайные польские порталы.
            // На частые запросы DuckDuckGo отвечает 202 с проверкой: обходить её не пытаемся, только ждём и пробуем ещё раз.
            let mut found = Vec::new();
            for attempt in 0..2 {
                if attempt > 0 {
                    tokio::time::sleep(Duration::from_secs(3)).await;
                }
                let resp = client().get("https://html.duckduckgo.com/html/").query(&[("kl", "ua-uk"), ("q", q.as_str())])
                    .header(reqwest::header::USER_AGENT, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15")
                    .send().await.map_err(|e| e.without_url().to_string())?;
                let limited = resp.status() == reqwest::StatusCode::ACCEPTED;
                found = search_results(&resp.text().await.map_err(|e| e.without_url().to_string())?, 4);
                if !found.is_empty() {
                    break;
                }
                if !limited {
                    return Err("пошук нічого не повернув, спробуй інші слова".into());
                }
            }
            if found.is_empty() {
                return Err("пошуковик тимчасово обмежив запити, спробуй за хвилину".into());
            }
            Ok(found.join(" | "))
        }
        Skill::IpInfo(ip) => {
            if !public_ip(ip) {
                return Ok("це локальна або внутрішня адреса, назовні не запитую".into());
            }
            let v = get_json(&format!("https://ipwho.is/{ip}"), &[]).await?;
            Ok(format!("{ip}: {}, {}, провайдер {}", v["country"].as_str().unwrap_or("?"), v["city"].as_str().unwrap_or("?"), v["connection"]["isp"].as_str().unwrap_or("?")))
        }
        Skill::Domain(d) => {
            let mut parts = Vec::new();
            for t in ["A", "NS", "MX"] {
                if let Ok(v) = get_json("https://dns.google/resolve", &[("name", d), ("type", t)]).await {
                    let vals: Vec<&str> = v["Answer"].as_array().map(|a| a.iter().filter_map(|x| x["data"].as_str()).take(4).collect()).unwrap_or_default();
                    if !vals.is_empty() {
                        parts.push(format!("{t}: {}", vals.join(", ")));
                    }
                }
            }
            if let Ok(v) = get_json(&format!("https://rdap.org/domain/{d}"), &[]).await {
                let reg = v["events"].as_array().and_then(|e| e.iter().find(|x| x["eventAction"] == "registration")).and_then(|x| x["eventDate"].as_str());
                if let Some(r) = reg {
                    parts.push(format!("зареєстровано {}", crate::util::truncate_chars(r, 10)));
                }
            }
            Ok(if parts.is_empty() { format!("{d}: записів не знайдено") } else { format!("{d}: {}", parts.join("; ")) })
        }
        Skill::Spotify(SpotifyCmd::Play) => spotify_play(None).await,
        Skill::Spotify(SpotifyCmd::Pause) => pause_media().await,
        Skill::Video { play } => video_control(*play).await,
        Skill::SysInfo => Ok(system_info().await),
        Skill::Spotify(cmd) => {
            // `tell` запустил бы закрытый Spotify, а просили только переключить трек или узнать, что играет.
            if !spotify_running().await {
                return Err("Spotify не запущений".into());
            }
            let script = match cmd {
                SpotifyCmd::Pause | SpotifyCmd::Play => unreachable!("обрабатываются выше"),
                SpotifyCmd::Next => "tell application \"Spotify\" to next track",
                SpotifyCmd::Previous => "tell application \"Spotify\" to previous track",
                SpotifyCmd::Current => "tell application \"Spotify\"\nset track_name to (name of current track) & \" — \" & (artist of current track)\nif player state is playing then return \"Зараз грає: \" & track_name\nreturn \"Нічого не грає, на паузі: \" & track_name\nend tell",
            };
            let out = tokio::time::timeout(Duration::from_secs(5), tokio::process::Command::new("osascript").arg("-e").arg(script).kill_on_drop(true).output())
                .await
                .map_err(|_| "Spotify не відповідає".to_string())?
                .map_err(|e| e.to_string())?;
            if !out.status.success() {
                return Err("Spotify недоступний або не дозволено керування".into());
            }
            let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
            Ok(if text.is_empty() { "виконано".into() } else { text })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn intents() {
        assert_eq!(parse("погода у Києві?"), Some(Skill::Weather("Києві".into())));
        assert_eq!(parse("постав таймер на 5 хвилин"), Some(Skill::Timer(300)));
        assert_eq!(parse("таймер 30 секунд"), Some(Skill::Timer(30)));
        assert_eq!(parse("перевір ip 8.8.8.8"), Some(Skill::Arsenal { tool: "ip", target: "8.8.8.8".into() }));
        assert_eq!(parse("перевір ip 192.168.1.1"), Some(Skill::IpInfo("192.168.1.1".parse().unwrap())));
        assert_eq!(parse("whois example.com"), Some(Skill::Arsenal { tool: "domain", target: "example.com".into() }));
        assert_eq!(parse("dns example.com"), Some(Skill::Domain("example.com".into())));
        assert_eq!(parse("заголовки https://example.com/a"), Some(Skill::Arsenal { tool: "headers", target: "https://example.com/a".into() }));
        assert_eq!(parse("запусти відео про космос"), Some(Skill::PlayVideo("про космос".into())));
        assert_eq!(parse("відкрий якийсь відос на ютубі"), Some(Skill::PlayVideo(String::new())));
        assert_eq!(parse("запусти музику в спотіфай"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("запусти музику в спотіку"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("увімкни музику"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("запусти музику з мого плейлисту в спотіфай"), Some(Skill::SpotifyPlaylist(None)));
        assert_eq!(parse("запусти мій плейлист"), Some(Skill::SpotifyPlaylist(None)));
        assert_eq!(parse("Включи Spotify, музику якусь із мого плейлиста."), Some(Skill::SpotifyPlaylist(None)));
        assert_eq!(parse("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=623ab0970fbf4415"), Some(Skill::SpotifyLink("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M".into())));
        assert_eq!(parse("глянь яка гарна https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"), None);
        assert_eq!(parse("запусти плейлист робочий"), Some(Skill::SpotifyPlaylist(Some("робочий".into()))));
        assert_eq!(parse("увімкни в спотіфай Океан Ельзи"), Some(Skill::SpotifySearch("Океан Ельзи".into())));
        let id = "37i9dQZF1DXcBWIGoYBM5M";
        let link = format!("https://open.spotify.com/playlist/{id}?si=abc");
        assert_eq!(parse(&format!("запам'ятай плейлист робочий {link}")), Some(Skill::SpotifySave { name: Some("робочий".into()), uri: format!("spotify:playlist:{id}") }));
        assert_eq!(parse(&format!("запам'ятай плейлист {link}")), Some(Skill::SpotifySave { name: None, uri: format!("spotify:playlist:{id}") }));
        assert_eq!(parse(&format!("запусти {link}")), Some(Skill::SpotifyUri(format!("spotify:playlist:{id}"))));
        assert_eq!(parse("мені подобається музика у спотіфай"), None);
        assert_eq!(parse("запусти плейлист вечірній"), Some(Skill::SpotifyPlaylist(Some("вечірній".into()))));
        assert_eq!(parse("запусти відео про вулкани"), Some(Skill::PlayVideo("про вулкани".into())));
        assert_eq!(parse("відкрий спотіфай"), Some(Skill::OpenApp("спотіфай".into())));
        assert_eq!(parse("постав музику на паузу"), Some(Skill::Spotify(SpotifyCmd::Pause)));
        for stop in ["іиключи музику", "виключи музику", "вимкни музику", "выключи музыку", "зупини трек", "будь ласка вимкни спотіфай", "вируби музику"] {
            assert_eq!(parse(stop), Some(Skill::Spotify(SpotifyCmd::Pause)), "{stop}");
        }
        assert_eq!(parse("включи музику"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("не вимикай музику"), None);
        assert_eq!(parse("зроби паузу в роботі"), None);
        assert_eq!(parse("в мене пауза на обід"), None);
        assert_eq!(parse("пауза"), Some(Skill::Spotify(SpotifyCmd::Pause)));
        assert_eq!(parse("Юна, запусти таймер на 5 хвилин"), Some(Skill::Timer(300)));
        assert_eq!(parse("таймер на 1 годину 30 хвилин"), Some(Skill::Timer(5400)));
        assert_eq!(parse("таймер 90 сек"), Some(Skill::Timer(90)));
        assert_eq!(parse("Юно, будь ласка, відкрий мені ютуб."), Some(Skill::OpenUrl("https://www.youtube.com".into())));
        assert_eq!(parse("можеш увімкнути музику"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("включи музыку пожалуйста"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("відкрий відеоредактор"), Some(Skill::OpenApp("відеоредактор".into())));
        assert_eq!(parse("запусти на ютубі котів"), Some(Skill::PlayVideo("котів".into())));
        assert_eq!(parse("запусти музику на ютубі"), Some(Skill::PlayVideo("музику".into())));
        assert_eq!(parse("запусти ютуб"), Some(Skill::OpenUrl("https://www.youtube.com".into())));
        for misheard in ["Включі ютуб.", "відкрив у топ", "відкри утуб", "увімкне ютуб", "Відкрити ютуб"] {
            assert_eq!(parse(misheard), Some(Skill::OpenUrl("https://www.youtube.com".into())), "{misheard}");
        }
        assert_eq!(parse("Увімкне музику"), Some(Skill::Spotify(SpotifyCmd::Play)));
        assert_eq!(parse("виключи музику"), Some(Skill::Spotify(SpotifyCmd::Pause)));
        assert_eq!(parse("вимкни відео"), Some(Skill::Video { play: false }));
        assert_eq!(parse("Що у мене за комп'ютер?"), Some(Skill::SysInfo));
        assert_eq!(parse("Який процесор в мене на макбуці?"), Some(Skill::SysInfo));
        assert_eq!(parse("скільки оперативки"), Some(Skill::SysInfo));
        let pc = pc_summary("Apple M4 Pro", "Mac16,8", "12", 24 << 30, "27.0", "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t39%; discharging");
        assert_eq!(pc, "Модель Mac16,8, процесор Apple M4 Pro, ядер 12, оперативка 24 ГБ, macOS 27.0, заряд 39%");
        assert_eq!(disk_summary("Filesystem 1024-blocks Used Available\n/dev/x 100 50 2097152"), ", вільно на диску 2 ГБ");
        assert_eq!(parse("Постав ютуб на паузу."), Some(Skill::Video { play: false }));
        assert_eq!(parse("постав відео на паузу"), Some(Skill::Video { play: false }));
        assert_eq!(parse("зупини ютуб"), Some(Skill::Video { play: false }));
        assert_eq!(parse("продовж відео"), Some(Skill::Video { play: true }));
        assert_eq!(parse("зніми ютуб з паузи"), Some(Skill::Video { play: true }));
        assert_eq!(parse("Зупини Spotify, постав на паузу"), Some(Skill::Spotify(SpotifyCmd::Pause)));
        assert_eq!(parse("подзвони музику"), Some(Skill::Spotify(SpotifyCmd::Pause)));
        assert_eq!(parse("вчора дивився відео про паузу"), None);
        assert!(matches!(parse("запам'ятай плейлист"), Some(Skill::Explain(_))));
        assert_eq!(parse("попередній трек"), Some(Skill::Spotify(SpotifyCmd::Previous)));
        assert_eq!(parse("вчора дивився ютуб цілий день"), None);
        assert_eq!(parse("у мене карта банку заблокована"), None);
        assert_eq!(parse("покажи на карті Львів"), Some(Skill::Arsenal { tool: "maps", target: "Львів".into() }));
        assert_eq!(parse("яка погода сьогодні"), None);
        assert_eq!(parse("яка погода сьогодні у Києві"), Some(Skill::Weather("Києві".into())));
        assert_eq!(parse("покажи погоду в Одесі"), Some(Skill::Weather("Одесі".into())));
        assert_eq!(parse("знайди чайник"), Some(Skill::Search("чайник".into())));
        assert_eq!(parse("знайди нік людини"), None);
        assert_eq!(parse("відкрий ютуб"), Some(Skill::OpenUrl("https://www.youtube.com".into())));
        assert_eq!(parse("відкрий на ютубі lofi music"), Some(Skill::OpenUrl("https://www.youtube.com/results?search_query=lofi%20music".into())));
        assert_eq!(parse("відкрий https://example.com/a"), Some(Skill::OpenUrl("https://example.com/a".into())));
        assert_eq!(parse("відкрий сайт example.com"), Some(Skill::OpenUrl("https://example.com".into())));
        assert_eq!(parse("запусти Telegram"), Some(Skill::OpenApp("Telegram".into())));
        assert_eq!(parse("відкрий програму Spotify"), Some(Skill::OpenApp("Spotify".into())));
        assert_eq!(parse("відкрий file:///etc/passwd").map(|s| matches!(s, Skill::OpenUrl(_))), Some(false));
        assert!(!matches!(parse("я хочу відкрий ютуб колись"), Some(Skill::OpenUrl(_) | Skill::OpenApp(_))));
        assert_eq!(parse("прочитай https://example.com"), Some(Skill::Arsenal { tool: "page", target: "https://example.com".into() }));
        assert_eq!(parse("вебархів example.com"), Some(Skill::Arsenal { tool: "wayback", target: "example.com".into() }));
        assert_eq!(parse("ютуб lofi music"), Some(Skill::Arsenal { tool: "youtube", target: "lofi music".into() }));
        assert_eq!(parse("постав на паузу"), Some(Skill::Spotify(SpotifyCmd::Pause)));
        assert_eq!(parse("пошукай новини rust"), Some(Skill::Search("новини rust".into())));
        assert_eq!(parse("знайди рецепт борщу"), Some(Skill::Search("рецепт борщу".into())));
        assert_eq!(parse("як справи"), None);
        assert_eq!(parse("ти дурна"), None);
    }

    #[test]
    fn spotify_links_and_playlists() {
        let id = "37i9dQZF1DXcBWIGoYBM5M";
        assert_eq!(spotify_uri(&format!("https://open.spotify.com/intl-uk/album/{id}")), Some(format!("spotify:album:{id}")));
        for bad in ["https://evil.example/playlist/37i9dQZF1DXcBWIGoYBM5M", "https://open.spotify.com/user/abc", "https://open.spotify.com/playlist/short", "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYB\"x"] {
            assert_eq!(spotify_uri(bad), None, "{bad}");
        }
        assert!(valid_spotify_uri(&format!("spotify:track:{id}")));
        assert!(!valid_spotify_uri("spotify:track:x\" & do shell script \"id"));
        let dir = std::env::temp_dir().join(format!("yuna-sp-{}", crate::util::now_ms()));
        let path = dir.join("spotify.json");
        assert!(find_playlist(&path, None).unwrap_err().contains("запам'ятай"));
        let uri = format!("spotify:playlist:{id}");
        save_playlist(&path, Some("робочий"), &uri).unwrap();
        assert_eq!(find_playlist(&path, Some("робочий")).unwrap(), uri);
        assert_eq!(find_playlist(&path, None).unwrap(), uri, "первый плейлист становится основным");
        assert!(find_playlist(&path, Some("вечірній")).is_err());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn open_guards() {
        assert!(safe_url("https://www.youtube.com/results?search_query=a%20b"));
        for bad in ["file:///etc/passwd", "javascript:alert(1)", "http://user:pw@example.com/", "https://exa mple.com", "ftp://example.com", "http://localhost:8787/", "-a"] {
            assert!(!safe_url(bad), "{bad}");
        }
    }

    #[test]
    fn app_lookup_is_exact_and_denies_shells() {
        let dir = std::env::temp_dir().join(format!("yuna-apps-{}", crate::util::now_ms()));
        for app in ["Forkgram.app", "Terminal.app", "Notes.app"] {
            std::fs::create_dir_all(dir.join(app)).unwrap();
        }
        let dirs = vec![dir.clone()];
        assert!(resolve_app("телеграм", &dirs).unwrap().ends_with("Forkgram.app"));
        assert!(resolve_app("нотатки", &dirs).is_ok());
        assert!(resolve_app("Terminal", &dirs).unwrap_err().contains("не відкриваю"));
        assert!(resolve_app("термінал", &dirs).unwrap_err().contains("не відкриваю"));
        for bad in ["../Terminal", "/bin/sh", "Tele", "Forkgram.app"] {
            assert!(resolve_app(bad, &dirs).is_err(), "{bad}");
        }
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn base64_matches_standard() {
        assert_eq!(base64_encode(b""), "");
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
        assert_eq!(base64_encode(&[255, 254, 253]), "//79");
        assert_eq!(human_time(5400), "1 год 30 хв");
        assert_eq!(human_time(90), "1 хв 30 с");
    }

    #[tokio::test]
    async fn unknown_or_too_many_recipients_send_nothing() {
        let many = Skill::TgDraft { to: "@aaaaa1 @aaaaa2 @aaaaa3 @aaaaa4 @aaaaa5 @aaaaa6".into(), text: "x".into() };
        assert!(run(&many, false).await.unwrap_err().contains("не більше"));
        let unknown = Skill::TgDraft { to: "@aaaaa1 незнайомцю".into(), text: "x".into() };
        assert!(run(&unknown, false).await.unwrap_err().contains("не знаю"));
    }

    #[tokio::test]
    async fn incognito_refuses_account_apps() {
        for skill in [Skill::SpotifySearch("x".into()), Skill::TgDraft { to: "@sasha_nick".into(), text: "x".into() }, Skill::Call { to: "+380991234567".into(), telegram: false }, Skill::DiscordVoice("x".into())] {
            assert!(run(&skill, true).await.unwrap_err().contains("інкогніто"), "{skill:?}");
        }
        assert!(run(&Skill::Indicators("8.8.8.8".into()), true).await.is_ok());
    }

    #[test]
    fn vpn_status_fails_closed() {
        let on = r#"{"state":"connected","details":{"location":{"country":"Poland","city":"Warsaw","mullvad_exit_ip":true}}}"#;
        assert!(vpn_summary(on).unwrap().contains("Warsaw"));
        assert!(vpn_summary(&on.replace("true", "false")).is_err());
        assert!(vpn_summary(r#"{"state":"disconnected"}"#).is_err());
        assert!(vpn_summary("не json").is_err());
        assert_eq!(parse("перевір впн"), Some(Skill::Vpn { connect: false }));
        assert_eq!(parse("увімкни впн"), Some(Skill::Vpn { connect: true }));
        assert!(matches!(parse("вимкни впн"), Some(Skill::Explain(_))));
        assert_eq!(parse("відкрий mullvad browser"), Some(Skill::OpenApp("mullvad browser".into())));
    }

    #[test]
    fn messaging_commands() {
        let draft = |to: &str, text: &str| Some(Skill::TgDraft { to: to.into(), text: text.into() });
        assert_eq!(parse("напиши в тг Саші привіт як справи"), draft("Саші", "привіт як справи"));
        assert_eq!(parse("напиши Саші в телеграм що я спізнюсь"), draft("Саші", "я спізнюсь"));
        assert_eq!(parse("напиши @sasha_nick у тг Привіт!"), draft("@sasha_nick", "Привіт!"));
        assert_eq!(parse("напиши в форкграм Саші привіт"), draft("Саші", "привіт"));
        assert_eq!(parse("@sasha_nick напиши йому що я спізнюсь"), draft("@sasha_nick", "я спізнюсь"));
        assert_eq!(parse("@sasha_nick напиши у тг йому привіт"), draft("@sasha_nick", "привіт"));
        assert_eq!(parse("напиши @sasha_nick привіт"), draft("@sasha_nick", "привіт"));
        assert_eq!(parse("напиши олегу в тг що я спізнюсь"), draft("олегу", "я спізнюсь"));
        assert!(matches!(parse("напиши йому в тг привіт"), Some(Skill::Explain(_))));
        assert_eq!(parse("напиши мені вірш"), None);
        let compose = |to: &str, about: &str| Some(Skill::TgCompose { to: to.into(), about: about.into() });
        assert_eq!(parse("напиши щось @test_nick"), compose("@test_nick", ""));
        assert_eq!(parse("напиши @test_nick щось смішне"), compose("@test_nick", "смішне"));
        assert_eq!(parse("напиши щось смішне @test_nick"), compose("@test_nick", "смішне"));
        assert_eq!(parse("@test_nick напиши йому щось про каву"), compose("@test_nick", "про каву"));
        assert_eq!(parse("напиши в тг щось @test_nick"), compose("@test_nick", ""));
        assert_eq!(parse("придумай щось про каву і напиши @test_nick"), compose("@test_nick", "про каву"));
        assert_eq!(parse("придумай і напиши @test_nick привітання"), compose("@test_nick", "привітання"));
        assert_eq!(parse("напиши привіт @test_nick"), draft("@test_nick", "привіт"));
        assert_eq!(parse("напиши @test_nick @coldrugses доброго ранку"), draft("@test_nick @coldrugses", "доброго ранку"));
        assert_eq!(parse("напиши @test_nick і @coldrugses, доброго ранку"), draft("@test_nick @coldrugses", "доброго ранку"));
        assert_eq!(parse("напиши @test_nick, @coldrugses доброго ранку"), draft("@test_nick @coldrugses", "доброго ранку"));
        assert_eq!(parse("напиши доброго ранку @test_nick та @coldrugses"), draft("@test_nick @coldrugses", "доброго ранку"));
        assert_eq!(parse("@test_nick @coldrugses напиши їм привіт"), draft("@test_nick @coldrugses", "привіт"));
        assert_eq!(parse("напиши щось @test_nick @coldrugses"), compose("@test_nick @coldrugses", ""));
        assert_eq!(parse("напиши @test_nick я і ти"), draft("@test_nick", "я і ти"));
        assert_eq!(parse("напиши щось"), None);
        assert_eq!(parse("нагадай через 10 хвилин подзвонити мамі"), Some(Skill::Remind { secs: 600, text: "подзвонити мамі".into() }));
        assert_eq!(parse("нагадай мені через 1 годину 30 хвилин про зустріч"), Some(Skill::Remind { secs: 5400, text: "зустріч".into() }));
        assert_eq!(parse("нагадай випити воду через півгодини"), Some(Skill::Remind { secs: 1800, text: "випити воду".into() }));
        assert_eq!(parse("нагадай через 5"), Some(Skill::Remind { secs: 300, text: "час вийшов".into() }));
        assert!(matches!(parse("нагадай подзвонити"), Some(Skill::Explain(_))));
        assert_eq!(parse("гучність 30"), Some(Skill::Volume(VolumeCmd::Set(30))));
        assert_eq!(parse("постав гучність на 50 відсотків"), Some(Skill::Volume(VolumeCmd::Set(50))));
        assert_eq!(parse("зроби тихіше"), Some(Skill::Volume(VolumeCmd::Down)));
        assert_eq!(parse("вимкни звук"), Some(Skill::Volume(VolumeCmd::Mute(true))));
        assert_eq!(parse("увімкни звук"), Some(Skill::Volume(VolumeCmd::Mute(false))));
        assert_eq!(parse("говори тихіше будь ласка і повільніше"), None);
        assert_eq!(parse("закрий Safari"), Some(Skill::QuitApp("Safari".into())));
        assert_eq!(parse("закрий програму Google Chrome"), Some(Skill::QuitApp("Google Chrome".into())));
        assert_eq!(parse("що на екрані?"), Some(Skill::ScreenLook));
        assert_eq!(parse("що ти вмієш"), Some(Skill::Help));
        assert_eq!(parse("щоти вмієш."), Some(Skill::Help));
        assert_eq!(parse("що ти вмієш робити?"), Some(Skill::Help));
        assert_eq!(parse("відкрий щось цікаве"), Some(Skill::PlayVideo(String::new())));
        assert_eq!(parse("покажи щось про космос"), Some(Skill::PlayVideo("про космос".into())));
        assert!(!matches!(parse("увімкни щось послухати"), Some(Skill::PlayVideo(_))));
        assert_eq!(parse("напиши щось смішне"), None);
        assert!(matches!(parse("напиши в тг щось"), Some(Skill::Explain(_))));
        assert_eq!(app_alias("телеграм"), "forkgram");
        assert!(matches!(parse("напиши в тг Саші"), Some(Skill::Explain(_))));
        assert_eq!(parse("напиши вірш про море"), None);
        assert_eq!(parse("я написав у тг саші привіт"), None);
        assert_eq!(parse("подзвони Саші"), Some(Skill::Call { to: "Саші".into(), telegram: false }));
        assert_eq!(parse("подзвони в тг Саші"), Some(Skill::Call { to: "Саші".into(), telegram: true }));
        assert_eq!(parse("зайди в дс у войс друзі"), Some(Skill::DiscordVoice("друзі".into())));
        assert_eq!(parse("зайди у войс https://discord.com/channels/123456789012345678/223456789012345678"), Some(Skill::DiscordVoice("123456789012345678/223456789012345678".into())));
        assert_eq!(parse("запам'ятай контакт Саша @sasha_nick"), Some(Skill::SaveContact { name: "саша".into(), address: "@sasha_nick".into() }));
        assert_eq!(parse("запам'ятай контакт Саша +380991234567"), Some(Skill::SaveContact { name: "саша".into(), address: "+380991234567".into() }));
        assert!(matches!(parse("запам'ятай контакт Саша вулиця-Шевченка"), Some(Skill::Explain(_))));
        assert_eq!(parse("запам'ятай войс друзі https://discord.com/channels/123456789012345678/223456789012345678"), Some(Skill::SaveVoice { name: "друзі".into(), link: "123456789012345678/223456789012345678".into() }));
        assert!(matches!(parse("запам'ятай войс друзі https://evil.example/channels/1/2"), Some(Skill::Explain(_))));
    }

    #[test]
    fn contacts_and_links() {
        let mut book = Book::default();
        book.contacts.insert("саша".into(), vec!["@sasha_nick".into(), "+380991234567".into()]);
        book.contacts.insert("іван".into(), vec!["@ivan_nick".into()]);
        assert!(find_contact(&book, "Саші").unwrap().contains(&"@sasha_nick".to_string()));
        assert!(find_contact(&book, "Івану").is_ok());
        assert!(find_contact(&book, "Петрові").unwrap_err().contains("запам'ятай контакт"));
        book.contacts.insert("олег".into(), vec!["@oleg_nick".into()]);
        assert_eq!(find_contact(&book, "олегу").unwrap(), vec!["@oleg_nick"]);
        assert_eq!(find_contact(&book, "Олегу").unwrap(), vec!["@oleg_nick"]);
        book.contacts.insert("віра".into(), vec!["@vira_nick".into()]);
        assert!(find_contact(&book, "вірш").is_err());
        assert!(find_contact(&book, "Вірі").is_ok());
        assert_eq!(find_contact(&book, "+380991234567").unwrap(), vec!["+380991234567"]);
        book.contacts.insert("сашко".into(), vec!["@sashko_nick".into()]);
        assert!(find_contact(&book, "сашку").is_ok());
        assert_eq!(telegram_link("@sasha_nick", Some("при віт&x"), true), "tg://resolve?domain=sasha_nick&text=%D0%BF%D1%80%D0%B8%20%D0%B2%D1%96%D1%82%26x");
        assert_eq!(telegram_link("+380991234567", None, true), "tg://resolve?phone=380991234567");
        assert_eq!(telegram_link("@sasha_nick", None, false), "https://t.me/sasha_nick");
        for bad in ["@a", "@bad&domain=x", "380991234567", "+38099", "+38099x234567", "@sasha nick"] {
            assert_eq!(valid_address(bad), None, "{bad}");
        }
        assert_eq!(discord_channel("https://discord.com/channels/1/2"), None);
        assert_eq!(discord_channel("https://discord.com/channels/123456789012345678/x"), None);
    }

    #[test]
    fn no_people_lookup_skills() {
        assert_eq!(parse("знайди людину за ніком kurasaki"), None);
        assert_eq!(parse("проби вивести адресу kurasaki"), None);
        assert_eq!(parse("пробий нік kurasaki"), None);
    }

    #[test]
    fn domain_and_ip_guards() {
        assert!(valid_domain("example.com"));
        assert!(!valid_domain("exa mple.com") && !valid_domain("localhost") && !valid_domain("-a.com") && !valid_domain("1.2.3.4"));
        assert!(public_ip(&"8.8.8.8".parse().unwrap()));
        for p in ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"] {
            assert!(!public_ip(&p.parse().unwrap()), "{p}");
        }
    }

    #[test]
    fn city_forms() {
        assert_eq!(city_candidates("Києві")[0], "Київ");
        assert_eq!(city_candidates("Берліні"), vec!["Берліні", "Берлін"]);
        assert_eq!(city_candidates("Рим"), vec!["Рим"]);
    }

    #[test]
    fn indicators() {
        let found = extract_indicators("зайшов з 8.8.8.8, файл d41d8cd98f00b204e9800998ecf8427e на https://evil.example/x і admin@example.com");
        assert!(found.contains(&"IP: 8.8.8.8".to_string()));
        assert!(found.contains(&"MD5: d41d8cd98f00b204e9800998ecf8427e".to_string()));
        assert!(found.iter().any(|f| f.starts_with("URL:")));
        assert!(found.contains(&"email: admin@example.com".to_string()));
    }

    #[test]
    fn search_and_weather_parsing() {
        let html = r##"<a rel="nofollow" class="result__a" href="https://a.example">Перший &amp; <b>другий</b></a><a class="result__snippet" href="x">Опис <b>тут</b></a><a class="result__a" href="y">Ще</a>"##;
        assert_eq!(search_results(html, 4), vec!["Перший & другий: Опис тут", "Ще"]);
        assert!(search_results("<html>captcha</html>", 4).is_empty());
        let v = serde_json::json!({"current":{"temperature_2m":12.5,"wind_speed_10m":3.0,"weather_code":2}});
        assert!(weather_summary("Київ", &v).unwrap().contains("12.5"));
    }
}
