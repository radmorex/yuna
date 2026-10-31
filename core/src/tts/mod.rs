//! TTS-провайдеры: none | piper | edge | say (macOS) | silero | elevenlabs | openai.
use crate::config::Config;
use serde_json::json;
use std::time::Duration;

#[derive(Clone, Debug)]
pub struct Audio {
    pub bytes: Vec<u8>,
    pub mime: &'static str,
    pub ext: &'static str,
    pub duration_ms: u64,
}

pub enum Tts {
    None,
    Say { voice: String, tmp_dir: std::path::PathBuf },
    Piper { python: String, model: String, filter: String, tmp_dir: std::path::PathBuf },
    /// Piper читает текст, сайдкар RVC перекрашивает голос; без сайдкара остаётся Piper с обработкой.
    Rvc { http: reqwest::Client, url: String, python: String, model: String, filter: String, tmp_dir: std::path::PathBuf },
    Edge { bin: String, voice: String, rate: String, pitch: String, filter: String, tmp_dir: std::path::PathBuf },
    Silero { http: reqwest::Client, url: String, speaker: String },
    ElevenLabs { http: reqwest::Client, key: String, voice_id: String, model: String },
    OpenAi { http: reqwest::Client, key: String, model: String, voice: String, base_url: String },
}

impl Tts {
    pub fn from_config(c: &Config) -> (Tts, Option<String>) {
        let http = reqwest::Client::builder().timeout(Duration::from_secs(30)).build().expect("http client");
        match c.tts_provider.as_str() {
            "none" | "" => (Tts::None, None),
            "rvc" => (
                Tts::Rvc {
                    http,
                    url: c.rvc_url.clone(),
                    python: c.piper_python.clone(),
                    model: c.piper_model.clone(),
                    filter: c.piper_filter.clone(),
                    tmp_dir: std::path::PathBuf::from("data/tmp"),
                },
                None,
            ),
            "piper" => (
                Tts::Piper {
                    python: c.piper_python.clone(),
                    model: c.piper_model.clone(),
                    filter: c.piper_filter.clone(),
                    tmp_dir: std::path::PathBuf::from("data/tmp"),
                },
                None,
            ),
            "edge" => (
                Tts::Edge {
                    bin: c.edge_tts_bin.clone(),
                    voice: c.edge_tts_voice.clone(),
                    rate: c.edge_tts_rate.clone(),
                    pitch: c.edge_tts_pitch.clone(),
                    filter: c.edge_tts_filter.clone(),
                    tmp_dir: std::path::PathBuf::from("data/tmp"),
                },
                None,
            ),
            "say" => (Tts::Say { voice: c.say_voice.clone(), tmp_dir: std::path::PathBuf::from("data/tmp") }, None),
            "silero" => (Tts::Silero { http, url: c.silero_url.clone(), speaker: c.silero_speaker.clone() }, None),
            "elevenlabs" => match (&c.elevenlabs_api_key, c.elevenlabs_voice_id.is_empty()) {
                (Some(k), false) => (
                    Tts::ElevenLabs { http, key: k.clone(), voice_id: c.elevenlabs_voice_id.clone(), model: c.elevenlabs_model.clone() },
                    None,
                ),
                _ => (Tts::None, Some("TTS_PROVIDER=elevenlabs, но нет ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID — голос выключен".into())),
            },
            "openai" => match &c.openai_api_key {
                Some(k) => (
                    Tts::OpenAi {
                        http,
                        key: k.clone(),
                        model: c.openai_tts_model.clone(),
                        voice: c.openai_tts_voice.clone(),
                        base_url: c.openai_base_url.clone(),
                    },
                    None,
                ),
                None => (Tts::None, Some("TTS_PROVIDER=openai, но нет OPENAI_API_KEY — голос выключен".into())),
            },
            other => (Tts::None, Some(format!("неизвестный TTS_PROVIDER={other} — голос выключен"))),
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            Tts::None => "none",
            Tts::Say { .. } => "say",
            Tts::Rvc { .. } => "rvc",
            Tts::Piper { .. } => "piper",
            Tts::Edge { .. } => "edge",
            Tts::Silero { .. } => "silero",
            Tts::ElevenLabs { .. } => "elevenlabs",
            Tts::OpenAi { .. } => "openai",
        }
    }

    /// Синтез речи. `Ok(None)` — провайдер «none».
    pub async fn synth(&self, text: &str) -> Result<Option<Audio>, String> {
        let text = text.trim();
        if text.is_empty() {
            return Ok(None);
        }
        match self {
            Tts::None => Ok(None),
            Tts::Say { voice, tmp_dir } => say(voice, tmp_dir, text).await.map(Some),
            Tts::Rvc { http, url, python, model, filter, tmp_dir } => {
                let resp = http
                    .post(format!("{}/tts", url.trim_end_matches('/')))
                    .json(&json!({"text": text}))
                    .send()
                    .await;
                match resp {
                    Ok(r) if r.status().is_success() => {
                        let bytes = r.bytes().await.map_err(|e| format!("rvc: {e}"))?.to_vec();
                        Ok(Some(audio_from_bytes(bytes, text)))
                    }
                    other => {
                        eprintln!("[tts] rvc недоступен ({:?}), говорю голосом Piper", other.map(|r| r.status()).map_err(|e| e.is_connect()));
                        let raw = piper(python, model, tmp_dir, text).await?;
                        Ok(Some(apply_voice_filter(raw, filter).await))
                    }
                }
            }
            Tts::Piper { python, model, filter, tmp_dir } => {
                let raw = piper(python, model, tmp_dir, text).await?;
                Ok(Some(apply_voice_filter(raw, filter).await))
            }
            Tts::Edge { bin, voice, rate, pitch, filter, tmp_dir } => {
                let raw = edge(bin, voice, rate, pitch, tmp_dir, text).await?;
                Ok(Some(apply_voice_filter(raw, filter).await))
            }
            Tts::Silero { http, url, speaker } => {
                let resp = http
                    .post(format!("{}/tts", url.trim_end_matches('/')))
                    .json(&json!({"text": text, "speaker": speaker, "sample_rate": 48000}))
                    .send()
                    .await
                    .map_err(|e| format!("silero: сайдкар недоступен ({e}). Запущен ли tools/silero_server.py?"))?;
                bytes_to_audio(resp, "silero", text).await.map(Some)
            }
            Tts::ElevenLabs { http, key, voice_id, model } => {
                let resp = http
                    .post(format!("https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"))
                    .header("xi-api-key", key)
                    .json(&json!({"text": text, "model_id": model}))
                    .send()
                    .await
                    .map_err(|e| format!("elevenlabs: сеть: {e}"))?;
                bytes_to_audio(resp, "elevenlabs", text).await.map(Some)
            }
            Tts::OpenAi { http, key, model, voice, base_url } => {
                let resp = http
                    .post(format!("{}/audio/speech", base_url.trim_end_matches('/')))
                    .bearer_auth(key)
                    .json(&json!({"model": model, "voice": voice, "input": text, "response_format": "wav"}))
                    .send()
                    .await
                    .map_err(|e| format!("openai-tts: сеть: {e}"))?;
                bytes_to_audio(resp, "openai-tts", text).await.map(Some)
            }
        }
    }
}

/// Режет реплику на куски для параллельного синтеза. Короткие фразы склеиваются: отдельное «Так.» звучит рвано.
pub fn split_speech(text: &str) -> Vec<String> {
    const MIN_CHARS: usize = 40;
    let mut out: Vec<String> = Vec::new();
    let mut current = String::new();
    let chars: Vec<char> = text.chars().collect();
    for (i, &c) in chars.iter().enumerate() {
        current.push(c);
        let ends = matches!(c, '.' | '!' | '?' | '…') && chars.get(i + 1).is_none_or(|n| n.is_whitespace());
        if ends && current.chars().count() >= MIN_CHARS {
            out.push(current.trim().to_string());
            current.clear();
        }
    }
    let tail = current.trim();
    if !tail.is_empty() {
        match out.last_mut() {
            Some(last) if tail.chars().count() < MIN_CHARS => {
                last.push(' ');
                last.push_str(tail);
            }
            _ => out.push(tail.to_string()),
        }
    }
    out
}

async fn bytes_to_audio(resp: reqwest::Response, who: &str, text: &str) -> Result<Audio, String> {
    let status = resp.status();
    let bytes = resp.bytes().await.map_err(|e| format!("{who}: {e}"))?.to_vec();
    if !status.is_success() {
        let msg = String::from_utf8_lossy(&bytes[..bytes.len().min(300)]).to_string();
        return Err(format!("{who}: HTTP {status}: {msg}"));
    }
    Ok(audio_from_bytes(bytes, text))
}

/// Определяет формат по сигнатуре и оценивает длительность.
pub fn audio_from_bytes(bytes: Vec<u8>, text: &str) -> Audio {
    if bytes.starts_with(b"RIFF") {
        let duration_ms = wav_duration_ms(&bytes).unwrap_or_else(|| estimate_speech_ms(text));
        Audio { bytes, mime: "audio/wav", ext: "wav", duration_ms }
    } else {
        Audio { bytes, mime: "audio/mpeg", ext: "mp3", duration_ms: estimate_speech_ms(text) }
    }
}

/// Длительность WAV по заголовку (byte rate + размер чанка data).
pub fn wav_duration_ms(b: &[u8]) -> Option<u64> {
    if b.len() < 44 || &b[0..4] != b"RIFF" || &b[8..12] != b"WAVE" {
        return None;
    }
    let mut pos = 12;
    let mut byte_rate = 0u32;
    while pos + 8 <= b.len() {
        let id = &b[pos..pos + 4];
        let size = u32::from_le_bytes(b[pos + 4..pos + 8].try_into().ok()?) as usize;
        if id == b"fmt " && pos + 20 <= b.len() {
            byte_rate = u32::from_le_bytes(b[pos + 16..pos + 20].try_into().ok()?);
        }
        if id == b"data" {
            if byte_rate == 0 {
                return None;
            }
            let size = size.min(b.len() - pos - 8);
            return Some(size as u64 * 1000 / byte_rate as u64);
        }
        pos += 8 + size + (size & 1);
    }
    None
}

/// Оценка длительности речи по тексту (~ 14 символов/сек для русского).
pub fn estimate_speech_ms(text: &str) -> u64 {
    let n = text.chars().filter(|c| c.is_alphanumeric()).count() as u64;
    (n * 1000 / 14).clamp(800, 30_000)
}

/// Локальный голос Piper: работает без сети, текст никуда не уходит.
async fn piper(python: &str, model: &str, tmp_dir: &std::path::Path, text: &str) -> Result<Audio, String> {
    use tokio::io::AsyncWriteExt;
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    tokio::fs::create_dir_all(tmp_dir).await.map_err(|e| format!("piper: {e}"))?;
    let n = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let path = tmp_dir.join(format!("piper-{}-{n}.wav", crate::util::now_ms()));
    let mut child = tokio::process::Command::new(python)
        .args(["-m", "piper", "-m", model, "-f"])
        .arg(&path)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("piper: не запускается {python} ({e}). Установите голос: npm run voice"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(text.as_bytes()).await.map_err(|e| format!("piper: {e}"))?;
    }
    let out = tokio::time::timeout(Duration::from_secs(20), child.wait_with_output())
        .await
        .map_err(|_| "piper: таймаут".to_string())?
        .map_err(|e| format!("piper: {e}"))?;
    let bytes = tokio::fs::read(&path).await.unwrap_or_default();
    let _ = tokio::fs::remove_file(&path).await;
    if !out.status.success() || bytes.is_empty() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("piper: {}. Установите голос: npm run voice", err.lines().last().unwrap_or("нет аудио")));
    }
    Ok(audio_from_bytes(bytes, text))
}

/// Нейроголос Microsoft через CLI edge-tts: mp3 моно, 48 кбит/с.
/// Сервис случайно отвечает «нет аудио» примерно на каждый пятый запрос, поэтому до трёх попыток.
async fn edge(bin: &str, voice: &str, rate: &str, pitch: &str, tmp_dir: &std::path::Path, text: &str) -> Result<Audio, String> {
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    tokio::fs::create_dir_all(tmp_dir).await.map_err(|e| format!("edge: {e}"))?;
    let n = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let path = tmp_dir.join(format!("edge-{}-{n}.mp3", crate::util::now_ms()));
    let mut last_err = String::new();
    for _ in 0..3 {
        // Форма `--text=…` — чтобы текст, начинающийся с дефиса, не стал флагом.
        let run = tokio::process::Command::new(bin)
            .arg("--voice").arg(voice)
            .arg(format!("--rate={rate}"))
            .arg(format!("--pitch={pitch}"))
            .arg(format!("--text={text}"))
            .arg("--write-media").arg(&path)
            .kill_on_drop(true)
            .output();
        match tokio::time::timeout(Duration::from_secs(10), run).await {
            Err(_) => last_err = "таймаут".into(),
            // Бинарника нет — повторять бессмысленно.
            Ok(Err(e)) => return Err(format!("edge: не запускается {bin} ({e}). Установите голос: npm run voice")),
            Ok(Ok(out)) => {
                let bytes = tokio::fs::read(&path).await.unwrap_or_default();
                let _ = tokio::fs::remove_file(&path).await;
                if out.status.success() && !bytes.is_empty() {
                    let mut audio = audio_from_bytes(bytes, text);
                    audio.duration_ms = mp3_duration_ms(audio.bytes.len(), 48);
                    return Ok(audio);
                }
                last_err = String::from_utf8_lossy(&out.stderr).lines().last().unwrap_or("нет аудио").to_string();
            }
        }
    }
    let _ = tokio::fs::remove_file(&path).await;
    Err(format!("edge: {last_err}"))
}

fn mp3_duration_ms(len: usize, kbps: u64) -> u64 {
    len as u64 * 8 / kbps
}

/// Прогоняет mp3 через ffmpeg. Если обработка не удалась, отдаём исходный голос: реплика важнее эффекта.
async fn apply_voice_filter(raw: Audio, filter: &str) -> Audio {
    use tokio::io::AsyncWriteExt;
    if filter.trim().is_empty() {
        return raw;
    }
    let child = tokio::process::Command::new("ffmpeg")
        .args(["-v", "error", "-threads", "1", "-i", "pipe:0", "-af", filter, "-b:a", "128k", "-f", "mp3", "pipe:1"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn();
    let Ok(mut child) = child else { return raw };
    let Some(mut stdin) = child.stdin.take() else { return raw };
    let input = raw.bytes.clone();
    tokio::spawn(async move {
        let _ = stdin.write_all(&input).await;
    });
    match tokio::time::timeout(Duration::from_secs(10), child.wait_with_output()).await {
        Ok(Ok(out)) if out.status.success() && !out.stdout.is_empty() => Audio {
            duration_ms: mp3_duration_ms(out.stdout.len(), 128),
            bytes: out.stdout,
            mime: "audio/mpeg",
            ext: "mp3",
        },
        _ => raw,
    }
}

async fn say(voice: &str, tmp_dir: &std::path::Path, text: &str) -> Result<Audio, String> {
    use tokio::io::AsyncWriteExt;
    tokio::fs::create_dir_all(tmp_dir).await.map_err(|e| format!("say: {e}"))?;
    let path = tmp_dir.join(format!("say-{}.wav", crate::util::now_ms()));
    let mut child = tokio::process::Command::new("say")
        .arg("-v")
        .arg(voice)
        .arg("--file-format=WAVE")
        .arg("--data-format=LEI16@22050")
        .arg("-o")
        .arg(&path)
        .arg("-f")
        .arg("-")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("say: не удалось запустить (это macOS?): {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(text.as_bytes()).await.map_err(|e| format!("say: {e}"))?;
    }
    let out = tokio::time::timeout(Duration::from_secs(20), child.wait_with_output())
        .await
        .map_err(|_| "say: таймаут".to_string())?
        .map_err(|e| format!("say: {e}"))?;
    if !out.status.success() {
        return Err(format!("say: {}", String::from_utf8_lossy(&out.stderr)));
    }
    let bytes = tokio::fs::read(&path).await.map_err(|e| format!("say: {e}"))?;
    let _ = tokio::fs::remove_file(&path).await;
    Ok(audio_from_bytes(bytes, text))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wav(samples: u32, rate: u32) -> Vec<u8> {
        let data_len = samples * 2;
        let mut v = Vec::new();
        v.extend_from_slice(b"RIFF");
        v.extend_from_slice(&(36 + data_len).to_le_bytes());
        v.extend_from_slice(b"WAVEfmt ");
        v.extend_from_slice(&16u32.to_le_bytes());
        v.extend_from_slice(&1u16.to_le_bytes());
        v.extend_from_slice(&1u16.to_le_bytes());
        v.extend_from_slice(&rate.to_le_bytes());
        v.extend_from_slice(&(rate * 2).to_le_bytes());
        v.extend_from_slice(&2u16.to_le_bytes());
        v.extend_from_slice(&16u16.to_le_bytes());
        v.extend_from_slice(b"data");
        v.extend_from_slice(&data_len.to_le_bytes());
        v.resize(v.len() + data_len as usize, 0);
        v
    }

    #[test]
    fn wav_duration() {
        assert_eq!(wav_duration_ms(&wav(24_000, 48_000)), Some(500));
        assert_eq!(wav_duration_ms(b"nope"), None);
        let a = audio_from_bytes(wav(48_000, 48_000), "x");
        assert_eq!((a.mime, a.duration_ms), ("audio/wav", 1000));
        assert_eq!(audio_from_bytes(vec![0xFF, 0xFB, 0, 0], "привет мир").mime, "audio/mpeg");
    }

    #[test]
    fn provider_selection_without_keys() {
        let mut m = std::collections::HashMap::new();
        m.insert("TTS_PROVIDER".to_string(), "elevenlabs".to_string());
        let (t, warn) = Tts::from_config(&Config::from_map(&m));
        assert_eq!(t.name(), "none");
        assert!(warn.is_some());
        m.insert("TTS_PROVIDER".to_string(), "silero".to_string());
        assert_eq!(Tts::from_config(&Config::from_map(&m)).0.name(), "silero");
    }

    #[tokio::test]
    async fn piper_missing_is_error_not_panic() {
        let dir = std::env::temp_dir().join(format!("mira-piper-{}", crate::util::now_ms()));
        let t = Tts::Piper { python: "/nonexistent/python".into(), model: "m".into(), filter: String::new(), tmp_dir: dir.clone() };
        assert!(t.synth("привет").await.unwrap_err().contains("npm run voice"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn rvc_selected() {
        let mut m = std::collections::HashMap::new();
        m.insert("TTS_PROVIDER".to_string(), "rvc".to_string());
        assert_eq!(Tts::from_config(&Config::from_map(&m)).0.name(), "rvc");
    }

    #[test]
    fn speech_is_split_by_phrases() {
        let long = "Перша фраза достатньо довга, щоб іти окремо. Друга фраза теж достатньо довга для окремого куска. Так.";
        let parts = split_speech(long);
        assert_eq!(parts.len(), 2);
        assert!(parts[1].ends_with("Так."));
        assert_eq!(parts.join(" "), long);
        assert_eq!(split_speech("Коротко."), vec!["Коротко."]);
        assert_eq!(split_speech("Версія 1.5 вже тут, і це довге речення без розриву посередині."), vec!["Версія 1.5 вже тут, і це довге речення без розриву посередині."]);
        assert!(split_speech("  ").is_empty());
    }

    #[test]
    fn edge_selected_and_duration() {
        let mut m = std::collections::HashMap::new();
        m.insert("TTS_PROVIDER".to_string(), "edge".to_string());
        assert_eq!(Tts::from_config(&Config::from_map(&m)).0.name(), "edge");
        assert_eq!(mp3_duration_ms(54_000, 48), 9_000);
    }

    #[tokio::test]
    async fn edge_missing_binary_is_error_not_panic() {
        let dir = std::env::temp_dir().join(format!("mira-edge-{}", crate::util::now_ms()));
        let t = Tts::Edge { bin: "/nonexistent/edge-tts".into(), voice: "v".into(), rate: "+0%".into(), pitch: "+0Hz".into(), filter: String::new(), tmp_dir: dir.clone() };
        assert!(t.synth("привет").await.unwrap_err().contains("npm run voice"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn broken_filter_keeps_original_voice() {
        let raw = audio_from_bytes(vec![0xFF, 0xFB, 0, 0], "тест");
        assert_eq!(apply_voice_filter(raw.clone(), "").await.bytes, raw.bytes);
        assert_eq!(apply_voice_filter(raw.clone(), "нет_такого_фильтра").await.bytes, raw.bytes);
    }

    #[tokio::test]
    async fn none_produces_nothing() {
        assert!(Tts::None.synth("привет").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn silero_unreachable_is_error_not_panic() {
        let t = Tts::Silero { http: reqwest::Client::new(), url: "http://127.0.0.1:1".into(), speaker: "xenia".into() };
        assert!(t.synth("привет").await.is_err());
    }
}
