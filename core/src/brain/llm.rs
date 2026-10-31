//! LLM-провайдеры: anthropic | openai | mock.
use super::{mock::mock_reply, ReplyJob};
use crate::util::Rng;
use serde_json::{json, Value};
use std::sync::Mutex;
use std::time::Duration;

pub enum Llm {
    Mock { rng: Mutex<Rng> },
    Anthropic { http: reqwest::Client, key: String, model: String },
    /// Локальная модель через родной API Ollama: только он даёт ограничить память и время в памяти.
    Ollama { http: reqwest::Client, url: String, model: String, keep_alive: String, num_ctx: u32, num_predict: u32 },
    /// Claude через залогиненный CLI: без инструментов, без настроек проекта и без сохранения сессий.
    ClaudeCli { bin: String, model: String, warm: tokio::sync::Mutex<Option<WarmClaude>> },
    OpenAi { http: reqwest::Client, key: String, model: String, base_url: String },
}

impl Llm {
    pub fn from_config(c: &crate::config::Config) -> (Llm, Option<String>) {
        let http = reqwest::Client::builder().timeout(Duration::from_secs(90)).build().expect("http client");
        let mock = Llm::Mock { rng: Mutex::new(Rng::from_time()) };
        match c.llm_provider.as_str() {
            "anthropic" => match &c.anthropic_api_key {
                Some(k) => (Llm::Anthropic { http, key: k.clone(), model: c.anthropic_model.clone() }, None),
                None => (mock, Some("LLM_PROVIDER=anthropic, но нет ANTHROPIC_API_KEY — использую mock".into())),
            },
            "openai" => match &c.openai_api_key {
                Some(k) => (
                    Llm::OpenAi { http, key: k.clone(), model: c.openai_model.clone(), base_url: c.openai_base_url.clone() },
                    None,
                ),
                None => (mock, Some("LLM_PROVIDER=openai, но нет OPENAI_API_KEY — использую mock".into())),
            },
            "ollama" => (
                Llm::Ollama {
                    http,
                    url: c.ollama_url.clone(),
                    model: c.ollama_model.clone(),
                    keep_alive: c.ollama_keep_alive.clone(),
                    num_ctx: c.ollama_num_ctx,
                    num_predict: c.ollama_num_predict,
                },
                None,
            ),
            "claude" => (Llm::ClaudeCli { bin: c.claude_bin.clone(), model: c.claude_model.clone(), warm: tokio::sync::Mutex::new(None) }, None),
            "mock" => (mock, None),
            other => (mock, Some(format!("неизвестный LLM_PROVIDER={other} — использую mock"))),
        }
    }

    /// Заранее поднимает процесс, чтобы и первая реплика не ждала старта.
    pub async fn warm_up(&self, system: &str) {
        if let Llm::ClaudeCli { bin, model, warm } = self {
            *warm.lock().await = spawn_claude(bin, model, system).ok();
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            Llm::Mock { .. } => "mock",
            Llm::Ollama { .. } => "ollama",
            Llm::ClaudeCli { .. } => "claude",
            Llm::Anthropic { .. } => "anthropic",
            Llm::OpenAi { .. } => "openai",
        }
    }

    pub async fn reply(&self, system: &str, user_msg: &str, job: &ReplyJob) -> Result<String, String> {
        match self {
            Llm::Ollama { http, url, model, keep_alive, num_ctx, num_predict } => {
                let mut body = json!({
                    "model": model,
                    "stream": false,
                    "keep_alive": keep_alive,
                    "options": {"num_ctx": num_ctx, "num_predict": num_predict},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                });
                // Размышления Qwen3 съели бы весь лимит токенов и добавили бы секунды к ответу.
                if model.starts_with("qwen3") {
                    body["think"] = json!(false);
                }
                let resp = http
                    .post(format!("{}/api/chat", url.trim_end_matches('/')))
                    .json(&body)
                    .send()
                    .await
                    .map_err(|e| format!("ollama: сеть: {e}. Запущен ли Ollama?"))?;
                let status = resp.status();
                let v: Value = resp.json().await.map_err(|e| format!("ollama: json: {e}"))?;
                if !status.is_success() {
                    return Err(format!("ollama: HTTP {status}: {}", v["error"].as_str().unwrap_or("?")));
                }
                extract_ollama(&v).ok_or_else(|| "ollama: пустой ответ".into())
            }
            Llm::ClaudeCli { bin, model, warm } => claude_reply(warm, bin, model, system, user_msg).await,
            Llm::Mock { rng } => {
                let mut r = rng.lock().unwrap();
                Ok(mock_reply(job, &mut r))
            }
            Llm::Anthropic { http, key, model } => {
                let body = json!({
                    "model": model,
                    "max_tokens": 220,
                    "system": system,
                    "messages": [{"role": "user", "content": anthropic_content(user_msg, job)}],
                });
                let resp = http
                    .post("https://api.anthropic.com/v1/messages")
                    .header("x-api-key", key)
                    .header("anthropic-version", "2023-06-01")
                    .json(&body)
                    .send()
                    .await
                    .map_err(|e| format!("anthropic: сеть: {e}"))?;
                let status = resp.status();
                let v: Value = resp.json().await.map_err(|e| format!("anthropic: json: {e}"))?;
                if !status.is_success() {
                    return Err(format!("anthropic: HTTP {status}: {}", v["error"]["message"].as_str().unwrap_or("?")));
                }
                extract_anthropic(&v).ok_or_else(|| "anthropic: пустой ответ".into())
            }
            Llm::OpenAi { http, key, model, base_url } => {
                let body = json!({
                    "model": model,
                    "max_tokens": 220,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": openai_content(user_msg, job)},
                    ],
                });
                let resp = http
                    .post(format!("{}/chat/completions", base_url.trim_end_matches('/')))
                    .bearer_auth(key)
                    .json(&body)
                    .send()
                    .await
                    .map_err(|e| format!("openai: сеть: {e}"))?;
                let status = resp.status();
                let v: Value = resp.json().await.map_err(|e| format!("openai: json: {e}"))?;
                if !status.is_success() {
                    return Err(format!("openai: HTTP {status}: {}", v["error"]["message"].as_str().unwrap_or("?")));
                }
                extract_openai(&v).ok_or_else(|| "openai: пустой ответ".into())
            }
        }
    }
}

/// Заранее запущенный CLI, ждущий реплику: его старт занимает около 4 с, а сам запрос 1–2 с.
pub struct WarmClaude {
    system: String,
    child: tokio::process::Child,
    stdin: tokio::process::ChildStdin,
    stdout: tokio::io::BufReader<tokio::process::ChildStdout>,
}

/// Чат — недоверенный ввод, поэтому инструменты отключены полностью: `--tools ""`.
/// Запуск из пустой папки и `--setting-sources ""` не дают подхватить настройки и файлы проекта.
fn spawn_claude(bin: &str, model: &str, system: &str) -> Result<WarmClaude, String> {
    let dir = std::env::temp_dir().join("mira-claude-cwd");
    std::fs::create_dir_all(&dir).map_err(|e| format!("claude: {e}"))?;
    let mut child = tokio::process::Command::new(bin)
        .args(["-p", "--model", model, "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"])
        .args(["--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--system-prompt", system])
        .current_dir(&dir)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("claude: не запускается {bin}: {e}"))?;
    let stdin = child.stdin.take().ok_or("claude: нет stdin")?;
    let stdout = child.stdout.take().ok_or("claude: нет stdout")?;
    Ok(WarmClaude { system: system.to_string(), child, stdin, stdout: tokio::io::BufReader::new(stdout) })
}

/// Строка потока с итогом: `Ok(текст)`, `Err(причина)` или `None`, если это промежуточное событие.
pub fn parse_stream_result(line: &str) -> Option<Result<String, String>> {
    let v: Value = serde_json::from_str(line).ok()?;
    if v["type"] != "result" {
        return None;
    }
    let text = v["result"].as_str().unwrap_or("").trim().to_string();
    if v["is_error"].as_bool().unwrap_or(false) || text.is_empty() {
        return Some(Err(format!("claude: {}", crate::util::truncate_chars(&text, 200))));
    }
    Some(Ok(text))
}

async fn ask_claude(w: &mut WarmClaude, user_msg: &str) -> Result<String, String> {
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt};
    let line = json!({"type": "user", "message": {"role": "user", "content": user_msg}}).to_string() + "\n";
    w.stdin.write_all(line.as_bytes()).await.map_err(|e| format!("claude: {e}"))?;
    w.stdin.flush().await.map_err(|e| format!("claude: {e}"))?;
    let mut buf = String::new();
    loop {
        buf.clear();
        let n = w.stdout.read_line(&mut buf).await.map_err(|e| format!("claude: {e}"))?;
        if n == 0 {
            return Err("claude: процесс завершился".into());
        }
        if let Some(result) = parse_stream_result(&buf) {
            return result;
        }
    }
}

/// Отвечает готовым процессом и сразу запускает следующий. Каждая реплика идёт в новой сессии,
/// поэтому текст из чата не накапливается в контексте модели.
async fn claude_reply(slot: &tokio::sync::Mutex<Option<WarmClaude>>, bin: &str, model: &str, system: &str, user_msg: &str) -> Result<String, String> {
    let mut slot = slot.lock().await;
    let warm = slot.take().filter(|w| w.system == system);
    let reused = warm.is_some();
    let mut w = match warm {
        Some(w) => w,
        None => spawn_claude(bin, model, system)?,
    };
    let mut result = ask_claude(&mut w, user_msg).await;
    if result.is_err() && reused {
        // Готовый процесс мог умереть за время простоя.
        let mut fresh = spawn_claude(bin, model, system)?;
        result = ask_claude(&mut fresh, user_msg).await;
    }
    let _ = w.child.start_kill();
    *slot = spawn_claude(bin, model, system).ok();
    result
}

fn openai_content(text: &str, job: &ReplyJob) -> Value {
    match job.event.image.as_deref().filter(|u| crate::event::parse_image(u).is_some()) {
        Some(url) => json!([{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": url}}]),
        None => json!(text),
    }
}

fn anthropic_content(text: &str, job: &ReplyJob) -> Value {
    match job.event.image.as_deref().and_then(crate::event::parse_image) {
        Some((mime, data)) => json!([
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}},
            {"type": "text", "text": text},
        ]),
        None => json!(text),
    }
}

pub fn extract_ollama(v: &Value) -> Option<String> {
    let t = v["message"]["content"].as_str()?;
    // Qwen3 может вернуть размышления прямо в тексте: зрителю они не нужны.
    let t = t.rsplit("</think>").next().unwrap_or(t).trim();
    (!t.is_empty()).then(|| t.to_string())
}

pub fn extract_anthropic(v: &Value) -> Option<String> {
    let text: String = v["content"]
        .as_array()?
        .iter()
        .filter(|b| b["type"] == "text")
        .filter_map(|b| b["text"].as_str())
        .collect::<Vec<_>>()
        .join(" ");
    (!text.trim().is_empty()).then_some(text)
}

pub fn extract_openai(v: &Value) -> Option<String> {
    let t = v["choices"][0]["message"]["content"].as_str()?;
    (!t.trim().is_empty()).then(|| t.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_provider_responses() {
        let a = json!({"content":[{"type":"text","text":"[emotion:smug] Привет"}]});
        assert_eq!(extract_anthropic(&a).unwrap(), "[emotion:smug] Привет");
        let o = json!({"choices":[{"message":{"content":"Ку"}}]});
        assert_eq!(extract_openai(&o).unwrap(), "Ку");
        assert!(extract_openai(&json!({})).is_none());
    }

    #[test]
    fn ollama_needs_no_key() {
        let mut m = std::collections::HashMap::new();
        m.insert("LLM_PROVIDER".to_string(), "ollama".to_string());
        let (llm, warn) = Llm::from_config(&crate::config::Config::from_map(&m));
        assert!(warn.is_none());
        let Llm::Ollama { model, url, num_ctx, .. } = llm else { panic!("ожидался клиент Ollama") };
        assert_eq!(model, "qwen3:8b");
        assert!(url.contains("11434"));
        assert_eq!(num_ctx, 2048);
    }

    #[test]
    fn ollama_reply_parsing() {
        assert_eq!(extract_ollama(&json!({"message":{"content":"<think>хм</think>\n Ку"}})).unwrap(), "Ку");
        assert_eq!(extract_ollama(&json!({"message":{"content":"Ку"}})).unwrap(), "Ку");
        assert!(extract_ollama(&json!({"message":{"content":"  "}})).is_none());
    }

    #[test]
    fn photo_goes_as_multipart_only_when_valid() {
        let mut job = ReplyJob {
            event: crate::event::ChatEvent::new("control", "v", "x", true, "c"),
            question: "x".into(),
            memo_summary: String::new(),
            memo: None,
            injection: false,
            is_admin: true,
        };
        assert_eq!(openai_content("т", &job), json!("т"));
        job.event.image = Some("data:image/jpeg;base64,QUJD".into());
        assert_eq!(openai_content("т", &job)[1]["image_url"]["url"], "data:image/jpeg;base64,QUJD");
        assert_eq!(anthropic_content("т", &job)[0]["source"]["data"], "QUJD");
        job.event.image = Some("https://evil.example/x.jpg".into());
        assert_eq!(openai_content("т", &job), json!("т"));
    }

    #[test]
    fn stream_result_parsing() {
        assert_eq!(parse_stream_result(r#"{"type":"assistant","message":{}}"#), None);
        assert_eq!(parse_stream_result("не json"), None);
        assert_eq!(parse_stream_result(r#"{"type":"result","result":" Ку ","is_error":false}"#), Some(Ok("Ку".to_string())));
        assert!(matches!(parse_stream_result(r#"{"type":"result","result":"limit","is_error":true}"#), Some(Err(_))));
        assert!(matches!(parse_stream_result(r#"{"type":"result","result":""}"#), Some(Err(_))));
    }

    #[test]
    fn claude_cli_selected_without_key() {
        let mut m = std::collections::HashMap::new();
        m.insert("LLM_PROVIDER".to_string(), "claude".to_string());
        let (llm, warn) = Llm::from_config(&crate::config::Config::from_map(&m));
        assert_eq!(llm.name(), "claude");
        assert!(warn.is_none());
    }

    #[test]
    fn missing_key_falls_back_to_mock() {
        let mut m = std::collections::HashMap::new();
        m.insert("LLM_PROVIDER".to_string(), "anthropic".to_string());
        let (llm, warn) = Llm::from_config(&crate::config::Config::from_map(&m));
        assert_eq!(llm.name(), "mock");
        assert!(warn.is_some());
    }
}
