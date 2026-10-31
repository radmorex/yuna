//! HTTP-сервер: оверлей (статика + SSE), Control API, аудио, WebSocket-мост для Discord.
use crate::brain::guard::sanitize_input;
use crate::brain::Command;
use crate::event::{ChatEvent, Emotion, Outfit};
use crate::pipeline::Engine;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap, StatusCode, Uri};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::{stream, SinkExt, Stream, StreamExt};
use serde::Deserialize;
use serde_json::json;
use std::collections::HashMap;
use std::convert::Infallible;
use std::sync::Arc;
use tokio_stream::wrappers::BroadcastStream;

type St = State<Arc<Engine>>;

pub fn router(engine: Arc<Engine>) -> Router {
    Router::new()
        .route("/", get(|| async { Redirect::temporary("/overlay.html") }))
        .route("/control", get(|| async { Redirect::temporary("/control.html") }))
        .route("/health", get(|| async { Json(json!({"ok": true})) }))
        .route("/events", get(events))
        .route("/api/state", get(api_state))
        // Фото в запросе больше стандартного лимита 2 МБ.
        .route("/api/chat", post(api_chat).layer(axum::extract::DefaultBodyLimit::max(crate::event::MAX_IMAGE_BYTES + 100_000)))
        .route("/api/outfit", post(api_outfit))
        .route("/api/emotion", post(api_emotion))
        .route("/api/mute", post(api_mute))
        .route("/api/say", post(api_say))
        .route("/api/clear", post(api_clear))
        .route("/api/netmode", post(api_netmode))
        .route("/api/listen", post(api_listen))
        .route("/api/wake", post(api_wake))
        .route("/api/wake/command", post(api_wake_command))
        .route("/api/transcript", post(api_transcript))
        .route("/api/memory", get(api_memory))
        .route("/api/memory/note", post(api_memory_note))
        .route("/api/memory/forget", post(api_memory_forget))
        .route("/audio/{file}", get(audio))
        .route("/bridge", get(bridge))
        .fallback(static_file)
        .with_state(engine)
}

/// Разрешены только локальные Host (защита от DNS-rebinding) и, если задан, CONTROL_TOKEN.
fn check_control(e: &Engine, headers: &HeaderMap) -> Result<(), Response> {
    if !is_loopback(&e.hub.cfg.host) && e.hub.cfg.control_token.is_none() {
        return Err((StatusCode::FORBIDDEN, "HOST не loopback: задайте CONTROL_TOKEN").into_response());
    }
    if !local_host(headers, &e.hub.cfg.host) {
        return Err((StatusCode::FORBIDDEN, "host not allowed").into_response());
    }
    if let Some(tok) = &e.hub.cfg.control_token {
        let got = headers.get("x-control-token").and_then(|v| v.to_str().ok()).unwrap_or("");
        if !constant_eq(got.as_bytes(), tok.as_bytes()) {
            return Err((StatusCode::UNAUTHORIZED, "bad control token").into_response());
        }
    }
    Ok(())
}

pub fn is_loopback(host: &str) -> bool {
    matches!(host, "127.0.0.1" | "localhost" | "::1" | "[::1]")
}

pub fn local_host(headers: &HeaderMap, bind_host: &str) -> bool {
    let Some(host) = headers.get(header::HOST).and_then(|v| v.to_str().ok()) else { return true };
    let name = if host.starts_with('[') {
        host.split(']').next().map(|s| format!("{s}]")).unwrap_or_default()
    } else {
        host.split(':').next().unwrap_or("").to_string()
    };
    matches!(name.as_str(), "localhost" | "127.0.0.1" | "[::1]") || (bind_host != "0.0.0.0" && name == bind_host)
}

pub fn constant_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Поток событий и аудио содержат переписку: как и API, отдаём их только локальным Host.
async fn events(State(e): St, headers: HeaderMap) -> Response {
    if !local_host(&headers, &e.hub.cfg.host) {
        return (StatusCode::FORBIDDEN, "host not allowed").into_response();
    }
    event_stream(&e).into_response()
}

fn event_stream(e: &Engine) -> Sse<impl Stream<Item = Result<Event, Infallible>> + use<>> {
    let rx = e.hub.overlay.subscribe();
    let init = e.hub.state_json();
    let first = stream::once(async move { Ok(Event::default().data(init)) });
    let rest = BroadcastStream::new(rx).filter_map(|r| async move { r.ok().map(|s| Ok(Event::default().data(s))) });
    Sse::new(first.chain(rest)).keep_alive(KeepAlive::default())
}

async fn api_state(State(e): St, headers: HeaderMap) -> Response {
    if let Err(r) = check_control(&e, &headers) {
        return r;
    }
    let mut v: serde_json::Value = serde_json::from_str(&e.hub.state_json()).unwrap_or_default();
    // Показываем и модель: их у Ollama может быть несколько.
    v["llm"] = if e.hub.cfg.llm_provider == "ollama" { json!(format!("ollama · {}", e.hub.cfg.ollama_model)) } else { json!(e.llm.name()) };
    v["tts"] = json!(e.tts.name());
    v["sources"] = json!(e.hub.cfg.sources);
    v["memoryViewers"] = json!(e.brain.lock().unwrap().memory.len());
    Json(v).into_response()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ChatReq {
    user: String,
    text: String,
    #[serde(default)]
    is_admin: bool,
    #[serde(default)]
    image: Option<String>,
}

async fn api_chat(State(e): St, headers: HeaderMap, Json(r): Json<ChatReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    let user = sanitize_input(&r.user, 40);
    if user.is_empty() || r.text.trim().is_empty() {
        return (StatusCode::BAD_REQUEST, "user и text обязательны").into_response();
    }
    let mut ev = ChatEvent::new("control", &user, &r.text, r.is_admin, "control");
    ev.private = e.hub.incognito();
    if let Some(image) = r.image {
        // Фото ушло бы в облачную модель, а инкогнито обещает обратное.
        if e.hub.incognito() {
            return (StatusCode::CONFLICT, "В інкогніто фото не надсилаю").into_response();
        }
        if crate::event::parse_image(&image).is_none() {
            return (StatusCode::BAD_REQUEST, "Потрібне фото JPEG, PNG або WebP до 5 МБ").into_response();
        }
        ev.image = Some(image);
    }
    match e.hub.inbound.send(ev).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(_) => StatusCode::SERVICE_UNAVAILABLE.into_response(),
    }
}

#[derive(Deserialize)]
struct OutfitReq {
    outfit: String,
}
async fn api_outfit(State(e): St, headers: HeaderMap, Json(r): Json<OutfitReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    match Outfit::parse(&r.outfit) {
        Some(o) => {
            e.apply(Command::Outfit(o));
            Json(json!({"ok": true, "outfit": o.as_str()})).into_response()
        }
        None => (StatusCode::BAD_REQUEST, "outfit: hoodie | dress").into_response(),
    }
}

#[derive(Deserialize)]
struct EmotionReq {
    emotion: String,
}
async fn api_emotion(State(e): St, headers: HeaderMap, Json(r): Json<EmotionReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    match Emotion::parse(&r.emotion) {
        Some(em) => {
            e.apply(Command::Emotion(em));
            Json(json!({"ok": true, "emotion": em.as_str()})).into_response()
        }
        None => (StatusCode::BAD_REQUEST, "emotion: neutral | smug | surprised | angry | laugh").into_response(),
    }
}

#[derive(Deserialize)]
struct MuteReq {
    muted: bool,
}
async fn api_mute(State(e): St, headers: HeaderMap, Json(r): Json<MuteReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    e.apply(Command::Mute(r.muted));
    Json(json!({"ok": true, "muted": r.muted})).into_response()
}

#[derive(Deserialize)]
struct SayReq {
    text: String,
}
/// Юна произносит текст оператора (теги эмоций/наряда разрешены — это админ).
async fn api_say(State(e): St, headers: HeaderMap, Json(r): Json<SayReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    let job = crate::brain::ReplyJob {
        event: ChatEvent::new("control", "operator", &r.text, true, "control"),
        question: r.text.clone(),
        memo_summary: String::new(),
        memo: None,
        injection: false,
        is_admin: true,
    };
    let reply = e.brain.lock().unwrap().finalize(&sanitize_input(&r.text, 600), &job);
    match reply {
        Some(reply) => Json(e.speak(reply, None).await).into_response(),
        None => (StatusCode::BAD_REQUEST, "пустой текст").into_response(),
    }
}

async fn api_memory(State(e): St, headers: HeaderMap) -> Response {
    if let Err(r) = check_control(&e, &headers) {
        return r;
    }
    let brain = e.brain.lock().unwrap();
    let viewers: Vec<_> = brain
        .memory
        .recent_viewers(100)
        .into_iter()
        .map(|m| {
            json!({
                "user": m.display_name,
                "messages": m.messages,
                "lastSeen": m.last_seen,
                "topics": m.topics,
                "notes": m.notes,
                "recent": m.recent,
                "summary": brain.memory.summary(&m.display_name),
            })
        })
        .collect();
    Json(json!({"viewers": viewers, "total": brain.memory.len()})).into_response()
}

#[derive(Deserialize)]
struct NoteReq {
    user: String,
    note: String,
}
async fn api_memory_note(State(e): St, headers: HeaderMap, Json(r): Json<NoteReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    let note = sanitize_input(&r.note, 120);
    if note.is_empty() {
        return (StatusCode::BAD_REQUEST, "пустая заметка").into_response();
    }
    match e.brain.lock().unwrap().memory.add_note(&r.user, &note) {
        true => Json(json!({"ok": true})).into_response(),
        false => (StatusCode::NOT_FOUND, "зритель ещё не писал в чат").into_response(),
    }
}

#[derive(Deserialize)]
struct ForgetReq {
    user: String,
}
async fn api_memory_forget(State(e): St, headers: HeaderMap, Json(r): Json<ForgetReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    let removed = e.brain.lock().unwrap().memory.forget(&r.user);
    Json(json!({"ok": true, "removed": removed})).into_response()
}

async fn api_clear(State(e): St, headers: HeaderMap) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    e.hub.clear_chat();
    Json(json!({"ok": true})).into_response()
}

#[derive(Deserialize)]
struct NetModeReq {
    incognito: bool,
}
async fn api_netmode(State(e): St, headers: HeaderMap, Json(r): Json<NetModeReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    e.hub.set_incognito(r.incognito);
    if r.incognito && crate::skills::vpn_status().await.is_err() {
        e.hub.emit(json!({"type": "notice", "message": "Mullvad VPN не підключений: в інкогніто мережеві команди не працюватимуть"}));
    }
    Json(json!({"ok": true, "incognito": r.incognito})).into_response()
}

#[derive(Deserialize)]
struct ListenReq {
    enabled: bool,
    #[serde(default)]
    error: Option<String>,
}
async fn api_listen(State(e): St, headers: HeaderMap, Json(r): Json<ListenReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    if !e.hub.cfg.pc_control {
        return (StatusCode::FORBIDDEN, "PC_CONTROL вимкнено").into_response();
    }
    e.hub.set_listening(r.enabled);
    if let Some(msg) = r.error {
        e.hub.emit(json!({"type": "listenError", "message": sanitize_input(&msg, 200)}));
    }
    Json(json!({"ok": true, "listening": r.enabled})).into_response()
}

#[derive(Deserialize)]
struct WakeReq {
    #[serde(default)]
    enabled: Option<bool>,
    #[serde(default)]
    paused: Option<bool>,
}
/// Слово-активатор: включить, выключить или приостановить на время записи кнопкой.
async fn api_wake(State(e): St, headers: HeaderMap, Json(r): Json<WakeReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    if !e.hub.cfg.pc_control {
        return (StatusCode::FORBIDDEN, "PC_CONTROL вимкнено").into_response();
    }
    if let Some(on) = r.enabled {
        e.hub.set_wake(on);
        crate::pipeline::save_wake(on);
    }
    if let Some(paused) = r.paused {
        e.hub.set_wake_paused(paused);
    }
    Json(json!({"ok": true})).into_response()
}

#[derive(Deserialize)]
struct WakeCommandReq {
    text: String,
}
/// Команда со слова-активатора. Пустой текст: позвали по имени и ждут продолжения.
async fn api_wake_command(State(e): St, headers: HeaderMap, Json(r): Json<WakeCommandReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    if !e.hub.cfg.pc_control {
        return (StatusCode::FORBIDDEN, "PC_CONTROL вимкнено").into_response();
    }
    let text = sanitize_input(&r.text, 400);
    if text.is_empty() {
        e.hub.emit(json!({"type": "wakeReady"}));
        return Json(json!({"ok": true})).into_response();
    }
    e.hub.emit(json!({"type": "wakeHeard", "text": text}));
    let mut ev = ChatEvent::new("control", "власник", &format!("{}, {text}", e.hub.cfg.bot_name), true, "control");
    ev.private = e.hub.incognito();
    ev.voice = true;
    match e.hub.inbound.send(ev).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(_) => StatusCode::SERVICE_UNAVAILABLE.into_response(),
    }
}

#[derive(Deserialize)]
struct TranscriptReq {
    text: String,
}
/// Текст со звука ПК от слушателя. Сам текст не логируем: там могут быть чужие разговоры.
async fn api_transcript(State(e): St, headers: HeaderMap, Json(r): Json<TranscriptReq>) -> Response {
    if let Err(resp) = check_control(&e, &headers) {
        return resp;
    }
    if !e.hub.cfg.pc_control {
        return (StatusCode::FORBIDDEN, "PC_CONTROL вимкнено").into_response();
    }
    let text = sanitize_input(&r.text, 1000);
    if text.is_empty() || !e.hub.add_heard(&text, crate::util::now_ms()) {
        return StatusCode::CONFLICT.into_response();
    }
    Json(json!({"ok": true})).into_response()
}

async fn audio(State(e): St, headers: HeaderMap, Path(file): Path<String>) -> Response {
    if !local_host(&headers, &e.hub.cfg.host) {
        return (StatusCode::FORBIDDEN, "host not allowed").into_response();
    }
    let id = file.split('.').next().unwrap_or("");
    match e.hub.get_audio(id) {
        Some(a) => ([(header::CONTENT_TYPE, a.mime), (header::CACHE_CONTROL, "no-store")], a.bytes).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn static_file(State(e): St, uri: Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let path = if path.is_empty() { "overlay.html" } else { path };
    if path.contains('\\') || path.contains('\0') || path.split('/').any(|s| s.is_empty() || s == ".." || s.starts_with('.')) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let full = e.hub.cfg.public_dir.join(path);
    match tokio::fs::read(&full).await {
        Ok(bytes) => ([(header::CONTENT_TYPE, mime_for(path)), (header::CACHE_CONTROL, "no-cache")], bytes).into_response(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

pub fn mime_for(path: &str) -> &'static str {
    match path.rsplit('.').next().unwrap_or("").to_ascii_lowercase().as_str() {
        "html" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "json" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "vrm" | "glb" => "model/gltf-binary",
        "woff2" => "font/woff2",
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        _ => "application/octet-stream",
    }
}

/// Входящее сообщение от Discord-моста.
#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct BridgeIn {
    #[serde(rename = "type")]
    pub kind: String,
    pub user: String,
    pub text: String,
    #[serde(default)]
    pub is_admin: bool,
    #[serde(default)]
    pub channel_id: String,
    #[serde(default)]
    pub message_id: Option<String>,
    #[serde(default)]
    pub mentioned: bool,
    #[serde(default)]
    pub user_id: Option<String>,
}

/// Разбирает сообщение моста в `ChatEvent` (платформа всегда «discord»).
pub fn bridge_to_event(raw: &str) -> Option<ChatEvent> {
    let b: BridgeIn = serde_json::from_str(raw).ok()?;
    if b.kind != "chat" || b.user.trim().is_empty() || b.text.trim().is_empty() {
        return None;
    }
    let mut ev = ChatEvent::new("discord", &crate::util::truncate_chars(b.user.trim(), 40), &b.text, b.is_admin, &crate::util::truncate_chars(&b.channel_id, 32));
    ev.message_id = b.message_id.map(|m| crate::util::truncate_chars(&m, 32));
    // Бот пересылает только сообщения, адресованные Юне (и уже срезает «Юна,»), поэтому
    // каждое событие моста — обращение.
    ev.mentioned = true;
    ev.user_id = b.user_id.map(|u| crate::util::truncate_chars(&u, 32));
    Some(ev)
}

async fn bridge(State(e): St, Query(q): Query<HashMap<String, String>>, ws: WebSocketUpgrade) -> Response {
    // Мост передаёт isAdmin — без общего секрета его не открываем вовсе.
    let Some(tok) = &e.hub.cfg.bridge_token else {
        e.hub.log("[bridge] отклонено: BRIDGE_TOKEN не задан в .env");
        return (StatusCode::FORBIDDEN, "BRIDGE_TOKEN не задан").into_response();
    };
    let got = q.get("token").map(String::as_str).unwrap_or("");
    if !constant_eq(got.as_bytes(), tok.as_bytes()) {
        return (StatusCode::UNAUTHORIZED, "bad bridge token").into_response();
    }
    ws.on_upgrade(move |socket| bridge_session(socket, e))
}

async fn bridge_session(socket: WebSocket, e: Arc<Engine>) {
    e.hub.log("[bridge] Discord-бот подключился");
    let (mut tx, mut rx) = socket.split();
    let mut out = e.hub.bridge_out.subscribe();
    let send_task = tokio::spawn(async move {
        while let Ok(msg) = out.recv().await {
            if tx.send(Message::Text(msg.into())).await.is_err() {
                break;
            }
        }
    });
    while let Some(Ok(msg)) = rx.next().await {
        if let Message::Text(t) = msg {
            match bridge_to_event(t.as_str()) {
                Some(ev) => {
                    let _ = e.hub.inbound.send(ev).await;
                }
                None => e.hub.log("[bridge] некорректное сообщение, пропускаю"),
            }
        }
    }
    send_task.abort();
    e.hub.log("[bridge] Discord-бот отключился");
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    #[test]
    fn host_check() {
        let mut h = HeaderMap::new();
        h.insert(header::HOST, HeaderValue::from_static("127.0.0.1:8787"));
        assert!(local_host(&h, "127.0.0.1"));
        h.insert(header::HOST, HeaderValue::from_static("localhost:8787"));
        assert!(local_host(&h, "127.0.0.1"));
        h.insert(header::HOST, HeaderValue::from_static("evil.example.com"));
        assert!(!local_host(&h, "127.0.0.1"));
        h.insert(header::HOST, HeaderValue::from_static("[::1]:8787"));
        assert!(local_host(&h, "127.0.0.1"));
    }

    #[test]
    fn bridge_parsing() {
        let ev = bridge_to_event(r#"{"type":"chat","platform":"twitch","user":"anna","text":"Юна, привет","isAdmin":true,"channelId":"42","messageId":"7","mentioned":false}"#).unwrap();
        assert_eq!(ev.platform, "discord");
        assert!(ev.is_admin);
        assert_eq!(ev.message_id.as_deref(), Some("7"));
        assert!(ev.mentioned, "события моста всегда адресованы Юне");
        let ev = bridge_to_event(r#"{"type":"chat","user":"a","text":"b","userId":"77"}"#).unwrap();
        assert_eq!(ev.rate_key(), "discord:77");
        assert!(bridge_to_event(r#"{"type":"reply","user":"a","text":"b"}"#).is_none());
        assert!(bridge_to_event("not json").is_none());
        assert!(bridge_to_event(r#"{"type":"chat","user":"","text":"b"}"#).is_none());
    }

    #[test]
    fn constant_time_compare() {
        assert!(constant_eq(b"abc", b"abc"));
        assert!(!constant_eq(b"abc", b"abd"));
        assert!(!constant_eq(b"abc", b"ab"));
    }

    #[test]
    fn mime_types() {
        assert!(mime_for("overlay.html").starts_with("text/html"));
        assert!(mime_for("a.JS").starts_with("text/javascript"));
    }
}
