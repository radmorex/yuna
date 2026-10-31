use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// Разбор `.env`: `KEY=value`, комментарии `#`, кавычки, `export KEY=`.
pub fn parse_dotenv(src: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for raw in src.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line);
        let Some((k, v)) = line.split_once('=') else { continue };
        let key = k.trim().to_string();
        let mut val = v.trim().to_string();
        if (val.starts_with('"') && val.ends_with('"') && val.len() >= 2)
            || (val.starts_with('\'') && val.ends_with('\'') && val.len() >= 2)
        {
            val = val[1..val.len() - 1].to_string();
        } else if let Some(idx) = val.find(" #") {
            val = val[..idx].trim_end().to_string();
        }
        map.insert(key, val);
    }
    map
}

/// Загружает `.env` (если есть), затем поверх — переменные процесса.
pub fn load_env(path: &Path) -> HashMap<String, String> {
    let mut map = std::fs::read_to_string(path).map(|s| parse_dotenv(&s)).unwrap_or_default();
    for (k, v) in std::env::vars() {
        map.insert(k, v);
    }
    map
}

/// То же для Piper: другая частота дискретизации и сильнее сдвиг.
pub const PIPER_ANIME_FILTER: &str = "asetrate=22050*1.14,aresample=48000,atempo=0.92,equalizer=f=180:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.4:g=3,equalizer=f=9000:t=q:w=1:g=2,acompressor=threshold=-20dB:ratio=3:attack=4:release=70:makeup=2,alimiter=limit=0.95";

/// Обработка ffmpeg: голос выше и моложе, ярче и ровнее по громкости. Пустой EDGE_TTS_FILTER выключает.
pub const ANIME_VOICE_FILTER: &str = "asetrate=24000*1.12,aresample=48000,atempo=0.95,equalizer=f=180:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.4:g=3,equalizer=f=9000:t=q:w=1:g=2,acompressor=threshold=-20dB:ratio=3:attack=4:release=70:makeup=2,alimiter=limit=0.95";

#[derive(Clone, Debug)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub public_dir: PathBuf,
    pub control_token: Option<String>,
    /// Разрешить навыки, которые управляют компьютером. По умолчанию выключено.
    pub pc_control: bool,
    pub bridge_token: Option<String>,
    pub sources: Vec<String>,

    pub llm_provider: String,
    pub anthropic_api_key: Option<String>,
    pub anthropic_model: String,
    pub openai_api_key: Option<String>,
    pub openai_model: String,
    pub openai_base_url: String,
    pub ollama_url: String,
    pub claude_bin: String,
    pub claude_model: String,
    pub ollama_model: String,
    pub ollama_keep_alive: String,
    pub ollama_num_ctx: u32,
    pub ollama_num_predict: u32,
    pub rvc_url: String,

    pub tts_provider: String,
    pub say_voice: String,
    pub piper_python: String,
    pub piper_model: String,
    pub piper_filter: String,
    pub edge_tts_bin: String,
    pub edge_tts_voice: String,
    pub edge_tts_rate: String,
    pub edge_tts_pitch: String,
    pub edge_tts_filter: String,
    pub silero_url: String,
    pub silero_speaker: String,
    pub elevenlabs_api_key: Option<String>,
    pub elevenlabs_voice_id: String,
    pub elevenlabs_model: String,
    pub openai_tts_model: String,
    pub openai_tts_voice: String,

    pub twitch_channel: Option<String>,
    pub twitch_bot_username: Option<String>,
    pub twitch_oauth_token: Option<String>,
    pub twitch_client_id: Option<String>,
    pub twitch_client_secret: Option<String>,
    pub chat_reply_enabled: bool,

    pub admin_users: Vec<String>,
    pub bot_name: String,
    pub bot_name_acc: String,
    pub bot_aliases: Vec<String>,
    pub reply_cooldown_secs: u64,
    pub user_cooldown_secs: u64,
    pub user_max_per_minute: usize,
    pub max_reply_chars: usize,
    pub max_input_chars: usize,
    pub stop_words: Vec<String>,
    pub memory_file: PathBuf,
    pub mock_chat_interval_ms: u64,
    pub comment_interval_secs: u64,
    pub mock_viewers: u32,
}

fn opt(m: &HashMap<String, String>, k: &str) -> Option<String> {
    m.get(k).map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}
fn get(m: &HashMap<String, String>, k: &str, def: &str) -> String {
    opt(m, k).unwrap_or_else(|| def.to_string())
}
fn num<T: std::str::FromStr>(m: &HashMap<String, String>, k: &str, def: T) -> T {
    opt(m, k).and_then(|s| s.parse().ok()).unwrap_or(def)
}
fn list(m: &HashMap<String, String>, k: &str) -> Vec<String> {
    opt(m, k)
        .map(|s| s.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect())
        .unwrap_or_default()
}
fn flag(m: &HashMap<String, String>, k: &str, def: bool) -> bool {
    match opt(m, k).map(|s| s.to_lowercase()) {
        Some(s) => matches!(s.as_str(), "1" | "true" | "yes" | "on" | "да"),
        None => def,
    }
}

impl Config {
    pub fn from_map(m: &HashMap<String, String>) -> Config {
        let mut sources = list(m, "SOURCES");
        if sources.is_empty() {
            sources.push("mock".into());
        }
        let twitch_oauth_token = opt(m, "TWITCH_OAUTH_TOKEN").map(|t| {
            if t.starts_with("oauth:") { t } else { format!("oauth:{t}") }
        });
        Config {
            host: get(m, "HOST", "127.0.0.1"),
            port: num(m, "PORT", 8787),
            public_dir: PathBuf::from(get(m, "PUBLIC_DIR", "public")),
            control_token: opt(m, "CONTROL_TOKEN"),
            pc_control: get(m, "PC_CONTROL", "false").eq_ignore_ascii_case("true"),
            bridge_token: opt(m, "BRIDGE_TOKEN"),
            sources: sources.into_iter().map(|s| s.to_lowercase()).collect(),

            llm_provider: get(m, "LLM_PROVIDER", "mock").to_lowercase(),
            anthropic_api_key: opt(m, "ANTHROPIC_API_KEY"),
            anthropic_model: get(m, "ANTHROPIC_MODEL", "claude-haiku-5-5"),
            openai_api_key: opt(m, "OPENAI_API_KEY"),
            openai_model: get(m, "OPENAI_MODEL", "gpt-4o-mini"),
            openai_base_url: get(m, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
            claude_bin: get(m, "CLAUDE_BIN", "claude"),
            claude_model: get(m, "CLAUDE_MODEL", "haiku"),
            ollama_url: get(m, "OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model: get(m, "OLLAMA_MODEL", "qwen3:8b"),
            ollama_keep_alive: get(m, "OLLAMA_KEEP_ALIVE", "3m"),
            ollama_num_ctx: num(m, "OLLAMA_NUM_CTX", 2048),
            ollama_num_predict: num(m, "OLLAMA_NUM_PREDICT", 60),
            rvc_url: get(m, "RVC_URL", "http://127.0.0.1:8812"),

            tts_provider: get(m, "TTS_PROVIDER", "none").to_lowercase(),
            say_voice: get(m, "SAY_VOICE", "Milena"),
            piper_python: get(m, "PIPER_PYTHON", ".venv-tts/bin/python"),
            piper_model: get(m, "PIPER_MODEL", ".cache/piper/ru_RU-irina-medium.onnx"),
            piper_filter: m.get("PIPER_FILTER").cloned().unwrap_or_else(|| PIPER_ANIME_FILTER.to_string()),
            edge_tts_bin: get(m, "EDGE_TTS_BIN", ".venv-tts/bin/edge-tts"),
            edge_tts_voice: get(m, "EDGE_TTS_VOICE", "uk-UA-PolinaNeural"),
            edge_tts_rate: get(m, "EDGE_TTS_RATE", "+8%"),
            edge_tts_pitch: get(m, "EDGE_TTS_PITCH", "+12Hz"),
            edge_tts_filter: m.get("EDGE_TTS_FILTER").cloned().unwrap_or_default(),
            silero_url: get(m, "SILERO_URL", "http://127.0.0.1:8811"),
            silero_speaker: get(m, "SILERO_SPEAKER", "xenia"),
            elevenlabs_api_key: opt(m, "ELEVENLABS_API_KEY"),
            elevenlabs_voice_id: get(m, "ELEVENLABS_VOICE_ID", ""),
            elevenlabs_model: get(m, "ELEVENLABS_MODEL", "eleven_multilingual_v2"),
            openai_tts_model: get(m, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_tts_voice: get(m, "OPENAI_TTS_VOICE", "nova"),

            twitch_channel: opt(m, "TWITCH_CHANNEL").map(|c| c.trim_start_matches('#').to_lowercase()),
            twitch_bot_username: opt(m, "TWITCH_BOT_USERNAME").map(|s| s.to_lowercase()),
            twitch_oauth_token,
            twitch_client_id: opt(m, "TWITCH_CLIENT_ID"),
            twitch_client_secret: opt(m, "TWITCH_CLIENT_SECRET"),
            chat_reply_enabled: flag(m, "CHAT_REPLY_ENABLED", true),

            admin_users: list(m, "ADMIN_USERS").into_iter().map(|s| s.to_lowercase()).collect(),
            bot_name: get(m, "BOT_NAME", "Юна"),
            bot_name_acc: get(m, "BOT_NAME_ACC", "Юну"),
            bot_aliases: { let a = list(m, "BOT_ALIASES"); if a.is_empty() { vec!["Yuna".into()] } else { a } },
            reply_cooldown_secs: num(m, "REPLY_COOLDOWN_SECS", 5),
            user_cooldown_secs: num(m, "USER_COOLDOWN_SECS", 15),
            user_max_per_minute: num(m, "USER_MAX_PER_MINUTE", 3),
            max_reply_chars: num(m, "MAX_REPLY_CHARS", 500),
            max_input_chars: num(m, "MAX_INPUT_CHARS", 400),
            stop_words: list(m, "STOP_WORDS").into_iter().map(|s| s.to_lowercase()).collect(),
            memory_file: PathBuf::from(get(m, "MEMORY_FILE", "data/memory.json")),
            comment_interval_secs: num(m, "COMMENT_INTERVAL_SECS", 45),
            mock_chat_interval_ms: num(m, "MOCK_CHAT_INTERVAL_MS", 2600),
            mock_viewers: num(m, "MOCK_VIEWERS", 100),
        }
    }

    /// Имена, на которые она откликается, в нижнем регистре.
    pub fn trigger_names(&self) -> Vec<String> {
        std::iter::once(&self.bot_name).chain(&self.bot_aliases).map(|n| n.trim().to_lowercase()).filter(|n| !n.is_empty()).collect()
    }

    pub fn has_source(&self, name: &str) -> bool {
        self.sources.iter().any(|s| s == name)
    }

    /// Все секреты — для маскировки в логах.
    pub fn secrets(&self) -> Vec<String> {
        [
            &self.control_token,
            &self.bridge_token,
            &self.anthropic_api_key,
            &self.openai_api_key,
            &self.elevenlabs_api_key,
            &self.twitch_oauth_token,
            &self.twitch_client_secret,
        ]
        .into_iter()
        .flatten()
        .cloned()
        .collect()
    }

    pub fn channel_label(&self) -> String {
        match &self.twitch_channel {
            Some(c) => format!("twitch.tv/{c}"),
            None => "twitch.tv/yuna_demo".into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dotenv_parsing() {
        let m = parse_dotenv("# comment\nA=1\nexport B=\"two words\"\nC=x # tail\nD='q'\nbroken\n");
        assert_eq!(m["A"], "1");
        assert_eq!(m["B"], "two words");
        assert_eq!(m["C"], "x");
        assert_eq!(m["D"], "q");
        assert!(!m.contains_key("broken"));
    }

    #[test]
    fn defaults_are_mock() {
        let c = Config::from_map(&HashMap::new());
        assert_eq!(c.sources, vec!["mock"]);
        assert_eq!(c.llm_provider, "mock");
        assert_eq!(c.tts_provider, "none");
        assert_eq!(c.host, "127.0.0.1");
    }

    #[test]
    fn twitch_token_gets_prefix() {
        let mut m = HashMap::new();
        m.insert("TWITCH_OAUTH_TOKEN".to_string(), "abc123456789".to_string());
        m.insert("TWITCH_CHANNEL".to_string(), "#MagerDev".to_string());
        let c = Config::from_map(&m);
        assert_eq!(c.twitch_oauth_token.as_deref(), Some("oauth:abc123456789"));
        assert_eq!(c.twitch_channel.as_deref(), Some("magerdev"));
        assert_eq!(c.secrets().len(), 1);
    }
}
