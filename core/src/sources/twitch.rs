//! Twitch: чат через IRC-over-WebSocket (анонимно без токена), ответы при наличии токена,
//! счётчик зрителей через Helix при наличии client id.
use crate::event::ChatEvent;
use crate::pipeline::Hub;
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

pub const IRC_WS_URL: &str = "wss://irc-ws.chat.twitch.tv:443";
/// Лимит длины сообщения Twitch — 500 символов; оставляем запас.
pub const MAX_MSG_CHARS: usize = 450;

#[derive(Debug, Clone, PartialEq, Default)]
pub struct IrcMessage {
    pub tags: HashMap<String, String>,
    pub prefix: Option<String>,
    pub command: String,
    pub params: Vec<String>,
}

impl IrcMessage {
    pub fn nick(&self) -> Option<&str> {
        self.prefix.as_deref().map(|p| p.split('!').next().unwrap_or(p))
    }
}

pub fn unescape_tag(v: &str) -> String {
    let mut out = String::with_capacity(v.len());
    let mut it = v.chars();
    while let Some(c) = it.next() {
        if c == '\\' {
            match it.next() {
                Some(':') => out.push(';'),
                Some('s') => out.push(' '),
                Some('\\') => out.push('\\'),
                Some('r') => out.push('\r'),
                Some('n') => out.push('\n'),
                Some(o) => out.push(o),
                None => {}
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Разбор строки IRC (с IRCv3-тегами).
pub fn parse_line(line: &str) -> Option<IrcMessage> {
    let mut rest = line.trim_end_matches(['\r', '\n']);
    if rest.is_empty() {
        return None;
    }
    let mut msg = IrcMessage::default();
    if let Some(r) = rest.strip_prefix('@') {
        let (tags, r) = r.split_once(' ')?;
        for kv in tags.split(';') {
            let (k, v) = kv.split_once('=').unwrap_or((kv, ""));
            msg.tags.insert(k.to_string(), unescape_tag(v));
        }
        rest = r.trim_start();
    }
    if let Some(r) = rest.strip_prefix(':') {
        let (p, r) = r.split_once(' ')?;
        msg.prefix = Some(p.to_string());
        rest = r.trim_start();
    }
    let (head, trailing) = match rest.split_once(" :") {
        Some((h, t)) => (h, Some(t)),
        None => (rest, None),
    };
    let mut parts = head.split_whitespace();
    msg.command = parts.next()?.to_string();
    msg.params = parts.map(|s| s.to_string()).collect();
    if let Some(t) = trailing {
        msg.params.push(t.to_string());
    }
    Some(msg)
}

/// PRIVMSG → `ChatEvent`. Админ: broadcaster/moderator по бейджам или ник в списке.
pub fn to_event(msg: &IrcMessage, admins: &[String], bot_nick: Option<&str>) -> Option<ChatEvent> {
    if msg.command != "PRIVMSG" || msg.params.len() < 2 {
        return None;
    }
    let login = msg.nick()?.to_lowercase();
    if bot_nick.is_some_and(|b| b.eq_ignore_ascii_case(&login)) {
        return None;
    }
    let user = msg.tags.get("display-name").filter(|s| !s.is_empty()).cloned().unwrap_or_else(|| login.clone());
    let badges = msg.tags.get("badges").map(String::as_str).unwrap_or("");
    let by_badge = badges.split(',').any(|b| b.starts_with("broadcaster/") || b.starts_with("moderator/"));
    let is_admin = by_badge || msg.tags.get("mod").is_some_and(|m| m == "1") || admins.iter().any(|a| a == &login);
    let mut text = msg.params[1].clone();
    // /me действия приходят как \x01ACTION ...\x01
    if let Some(a) = text.strip_prefix("\u{1}ACTION ") {
        text = a.trim_end_matches('\u{1}').to_string();
    }
    let channel = msg.tags.get("room-id").cloned().unwrap_or_else(|| msg.params[0].clone());
    let mut ev = ChatEvent::new("twitch", &user, &text, is_admin, &channel);
    ev.message_id = msg.tags.get("id").cloned();
    Some(ev)
}

/// Готовит исходящий PRIVMSG: без переводов строк, с лимитом длины, опционально как ответ.
pub fn privmsg(channel: &str, text: &str, reply_to: Option<&str>) -> String {
    let clean: String = text.chars().map(|c| if c == '\r' || c == '\n' { ' ' } else { c }).collect();
    let clean = crate::util::truncate_chars(clean.trim(), MAX_MSG_CHARS);
    let reply = reply_to
        .filter(|id| id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
        .map(|id| format!("@reply-parent-msg-id={id} "))
        .unwrap_or_default();
    format!("{reply}PRIVMSG #{} :{clean}", channel.trim_start_matches('#'))
}

/// Число зрителей из ответа Helix `GET /streams`. Офлайн → 0.
pub fn parse_helix_viewers(v: &Value) -> u32 {
    v["data"].as_array().and_then(|a| a.first()).and_then(|s| s["viewer_count"].as_u64()).unwrap_or(0) as u32
}

pub fn anon_nick(seed: u64) -> String {
    format!("justinfan{}", 10_000 + seed % 89_999)
}

#[derive(Debug)]
pub struct OutMsg {
    pub text: String,
    pub reply_to: Option<String>,
}

/// Запускает чтение (и отправку, если есть токен) чата Twitch с переподключением.
pub async fn run(hub: Arc<Hub>) {
    let Some(channel) = hub.cfg.twitch_channel.clone() else {
        hub.log("[twitch] TWITCH_CHANNEL не задан — источник выключен");
        return;
    };
    let can_send = hub.cfg.twitch_oauth_token.is_some() && hub.cfg.twitch_bot_username.is_some() && hub.cfg.chat_reply_enabled;
    let (out_tx, mut out_rx) = mpsc::channel::<OutMsg>(16);
    if can_send {
        *hub.twitch_out.lock().unwrap() = Some(out_tx);
    }
    tokio::spawn(run_helix(hub.clone()));
    let mut backoff = 1u64;
    loop {
        hub.log(&format!(
            "[twitch] подключаюсь к #{channel} ({})",
            if can_send { "чтение + ответы" } else { "только чтение, анонимно" }
        ));
        match session(&hub, &channel, can_send, &mut out_rx).await {
            Ok(()) => backoff = 1,
            Err(e) => hub.log(&format!("[twitch] соединение потеряно: {e}")),
        }
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(60);
    }
}

async fn session(hub: &Arc<Hub>, channel: &str, can_send: bool, out_rx: &mut mpsc::Receiver<OutMsg>) -> Result<(), String> {
    let (ws, _) = tokio_tungstenite::connect_async(IRC_WS_URL).await.map_err(|e| e.to_string())?;
    let (mut tx, mut rx) = ws.split();
    let send = |s: String| Message::Text(s.into());
    tx.send(send("CAP REQ :twitch.tv/tags twitch.tv/commands".into())).await.map_err(|e| e.to_string())?;
    let bot_nick = if can_send {
        let token = hub.cfg.twitch_oauth_token.clone().unwrap_or_default();
        tx.send(send(format!("PASS {token}"))).await.map_err(|e| e.to_string())?;
        hub.cfg.twitch_bot_username.clone().unwrap_or_default()
    } else {
        anon_nick(crate::util::now_ms())
    };
    tx.send(send(format!("NICK {bot_nick}"))).await.map_err(|e| e.to_string())?;
    tx.send(send(format!("JOIN #{channel}"))).await.map_err(|e| e.to_string())?;
    let mut last_send = 0u64;
    loop {
        tokio::select! {
            frame = rx.next() => {
                let Some(frame) = frame else { return Err("сервер закрыл соединение".into()) };
                let frame = frame.map_err(|e| e.to_string())?;
                let Message::Text(text) = frame else { continue };
                for line in text.as_str().split("\r\n") {
                    let Some(msg) = parse_line(line) else { continue };
                    match msg.command.as_str() {
                        "PING" => {
                            let arg = msg.params.last().cloned().unwrap_or_default();
                            tx.send(send(format!("PONG :{arg}"))).await.map_err(|e| e.to_string())?;
                        }
                        "RECONNECT" => return Err("Twitch попросил переподключиться".into()),
                        "001" => hub.log("[twitch] авторизация успешна"),
                        "ROOMSTATE" => hub.log(&format!("[twitch] вошла в чат #{channel}")),
                        "NOTICE" => hub.log(&format!("[twitch] NOTICE: {}", msg.params.last().cloned().unwrap_or_default())),
                        "PRIVMSG" => {
                            if let Some(ev) = to_event(&msg, &hub.cfg.admin_users, Some(&bot_nick)) {
                                let _ = hub.inbound.send(ev).await;
                            }
                        }
                        _ => {}
                    }
                }
            }
            Some(out) = out_rx.recv(), if can_send => {
                // Не чаще 1 сообщения в 1.5 с — дополнительно к глобальному кулдауну.
                let wait = (last_send + 1500).saturating_sub(crate::util::now_ms());
                if wait > 0 { tokio::time::sleep(Duration::from_millis(wait)).await; }
                tx.send(send(privmsg(channel, &out.text, out.reply_to.as_deref()))).await.map_err(|e| e.to_string())?;
                last_send = crate::util::now_ms();
            }
        }
    }
}

/// Счётчик зрителей через Helix. Нужен TWITCH_CLIENT_ID и токен (user или app через client secret).
async fn run_helix(hub: Arc<Hub>) {
    let cfg = &hub.cfg;
    let (Some(client_id), Some(channel)) = (cfg.twitch_client_id.clone(), cfg.twitch_channel.clone()) else {
        hub.log("[twitch] TWITCH_CLIENT_ID не задан — счётчик зрителей Helix выключен");
        return;
    };
    let http = reqwest::Client::builder().timeout(Duration::from_secs(15)).build().expect("http");
    let mut bearer = cfg.twitch_oauth_token.as_ref().map(|t| t.trim_start_matches("oauth:").to_string());
    loop {
        if bearer.is_none() {
            if let Some(secret) = &cfg.twitch_client_secret {
                bearer = app_token(&http, &client_id, secret).await.map_err(|e| hub.log(&format!("[twitch] app token: {e}"))).ok();
            }
        }
        if let Some(tok) = &bearer {
            let r = http
                .get("https://api.twitch.tv/helix/streams")
                .query(&[("user_login", channel.as_str())])
                .header("Client-Id", &client_id)
                .bearer_auth(tok)
                .send()
                .await;
            match r {
                Ok(resp) if resp.status().as_u16() == 401 => {
                    hub.log("[twitch] Helix 401 — токен недействителен/не от этого client id");
                    bearer = None;
                }
                Ok(resp) => {
                    if let Ok(v) = resp.json::<Value>().await {
                        hub.set_viewers(parse_helix_viewers(&v));
                    }
                }
                Err(e) => hub.log(&format!("[twitch] Helix: {e}")),
            }
        } else {
            hub.log("[twitch] нет токена для Helix (нужен TWITCH_OAUTH_TOKEN или TWITCH_CLIENT_SECRET)");
            return;
        }
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}

async fn app_token(http: &reqwest::Client, id: &str, secret: &str) -> Result<String, String> {
    let v: Value = http
        .post("https://id.twitch.tv/oauth2/token")
        .form(&[("client_id", id), ("client_secret", secret), ("grant_type", "client_credentials")])
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    v["access_token"].as_str().map(String::from).ok_or_else(|| "нет access_token в ответе".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    const LINE: &str = "@badge-info=;badges=moderator/1,partner/1;color=#FF0000;display-name=Kurasaki;emotes=;id=abc-123;mod=1;room-id=1337;user-id=42 :kurasaki!kurasaki@kurasaki.tmi.twitch.tv PRIVMSG #magerdev1 :Юна, ты нейросеть?";

    #[test]
    fn parses_privmsg_with_tags() {
        let m = parse_line(LINE).unwrap();
        assert_eq!(m.command, "PRIVMSG");
        assert_eq!(m.nick(), Some("kurasaki"));
        assert_eq!(m.params, vec!["#magerdev1", "Юна, ты нейросеть?"]);
        assert_eq!(m.tags["display-name"], "Kurasaki");
        let ev = to_event(&m, &[], Some("justinfan1")).unwrap();
        assert_eq!(ev.user, "Kurasaki");
        assert!(ev.is_admin);
        assert_eq!(ev.channel_id, "1337");
        assert_eq!(ev.message_id.as_deref(), Some("abc-123"));
        assert_eq!(ev.platform, "twitch");
    }

    #[test]
    fn regular_viewer_not_admin_unless_listed() {
        let line = "@badges=subscriber/12;display-name=Rantug;mod=0 :rantug!rantug@x PRIVMSG #c :ахахах";
        let m = parse_line(line).unwrap();
        assert!(!to_event(&m, &[], None).unwrap().is_admin);
        assert!(to_event(&m, &["rantug".into()], None).unwrap().is_admin);
        let bc = parse_line("@badges=broadcaster/1 :boss!boss@x PRIVMSG #c :hi").unwrap();
        assert!(to_event(&bc, &[], None).unwrap().is_admin);
    }

    #[test]
    fn ignores_own_messages_and_other_commands() {
        let own = parse_line(":mira_bot!mira_bot@x PRIVMSG #c :я же Юна").unwrap();
        assert!(to_event(&own, &[], Some("mira_bot")).is_none());
        let ping = parse_line("PING :tmi.twitch.tv").unwrap();
        assert_eq!(ping.command, "PING");
        assert_eq!(ping.params, vec!["tmi.twitch.tv"]);
        assert!(to_event(&ping, &[], None).is_none());
    }

    #[test]
    fn tag_unescape_and_action() {
        assert_eq!(unescape_tag(r"a\sb\:c\\d"), "a b;c\\d");
        let m = parse_line(":u!u@x PRIVMSG #c :\u{1}ACTION машет рукой\u{1}").unwrap();
        assert_eq!(to_event(&m, &[], None).unwrap().text, "машет рукой");
    }

    #[test]
    fn outgoing_privmsg_is_safe() {
        assert_eq!(privmsg("#chan", "привет\r\nPRIVMSG #other :spam", None), "PRIVMSG #chan :привет  PRIVMSG #other :spam");
        assert_eq!(privmsg("chan", "ок", Some("abc-1")), "@reply-parent-msg-id=abc-1 PRIVMSG #chan :ок");
        assert!(!privmsg("c", "x", Some("bad id;x=y")).contains("reply-parent"));
        assert!(privmsg("c", &"я".repeat(900), None).chars().count() < 470);
    }

    #[test]
    fn helix_viewers() {
        let v: Value = serde_json::json!({"data":[{"viewer_count": 102}]});
        assert_eq!(parse_helix_viewers(&v), 102);
        assert_eq!(parse_helix_viewers(&serde_json::json!({"data":[]})), 0);
    }

    #[test]
    fn anon_nick_format() {
        assert!(anon_nick(5).starts_with("justinfan"));
    }
}
