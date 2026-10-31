//! Память о зрителях: JSON-файл, по каждому нику — счётчики, последние реплики, заметки.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

const MAX_RECENT: usize = 8;
const MAX_NOTES: usize = 10;
const MAX_VIEWERS: usize = 5_000;

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ViewerMemo {
    pub display_name: String,
    pub messages: u32,
    pub first_seen: u64,
    pub last_seen: u64,
    /// Последние реплики зрителя (новые в конце).
    pub recent: Vec<String>,
    /// Темы, которые зритель поднимал (id → сколько раз).
    #[serde(default)]
    pub topics: HashMap<String, u32>,
    /// Свободные заметки (их может добавлять админ или LLM-ответ).
    #[serde(default)]
    pub notes: Vec<String>,
}

impl ViewerMemo {
    pub fn topic_count(&self, id: &str) -> u32 {
        self.topics.get(id).copied().unwrap_or(0)
    }
    pub fn last_message(&self) -> Option<&str> {
        self.recent.last().map(|s| s.as_str())
    }
}

/// (id темы, ключевые слова, человекочитаемая заметка для промпта)
pub const TOPICS: &[(&str, &[&str], &str)] = &[
    ("ai_question", &["нейросет", "нейромереж", "ты бот", "ти бот", "ты ии", "ти ші", "ты ai", "ты живая", "ти жива", "ты реальн", "ти реальн", "нейронк", "кто за тебя", "за тебя пиш", "за тебе пиш", "ты робот", "ти робот"], "з'ясовує, чи вона нейромережа"),
    ("compliment", &["красив", "гарна", "милая", "мило", "мила", "люблю тебя", "кохаю", "голос приятн", "голос приємн", "лучшая", "найкраща"], "робив(ла) їй компліменти"),
    ("insult", &["тупая", "тупа", "дура", "глупая", "дурна", "бесполезн", "марна", "кринж"], "намагався(лась) її зачепити"),
    ("games", &["дота", "доту", " кс ", "cs2", "валорант", "майнкрафт", "игра", "гра", "катк"], "постійно говорить про ігри"),
    ("streamer", &["стример", "стрімер"], "обговорює стрімера"),
    ("outfit", &["переоденься", "переодягн", "платье", "сукн", "худи", "худі", "наряд", "вбрання"], "просить перевдягнутися"),
    // Заполняется не ключевыми словами, а через `mentions_minor`.
    ("minor", &[], "молодше 18: жодного флірту"),
    ("food", &["еда", "їжа", "пицц", "піц", "шаурм", "кушать", "голодн"], "думає про їжу"),
];

pub fn detect_topics(text: &str) -> Vec<&'static str> {
    let minor = super::guard::mentions_minor(text);
    // Пробелы по краям и вместо пунктуации — чтобы короткие ключи вроде « кс » ловили только целое слово.
    let t: String = format!(" {} ", text.to_lowercase())
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { ' ' })
        .collect();
    TOPICS
        .iter()
        .filter(|(_, keys, _)| keys.iter().any(|k| t.contains(k)))
        .map(|(id, _, _)| *id)
        .chain(minor.then_some("minor"))
        .collect()
}

pub struct Memory {
    path: Option<PathBuf>,
    viewers: HashMap<String, ViewerMemo>,
    dirty: bool,
}

impl Memory {
    pub fn in_memory() -> Self {
        Memory { path: None, viewers: HashMap::new(), dirty: false }
    }

    /// Загружает из файла; битый файл не роняет запуск (сохраняется копия `.bak`).
    pub fn load(path: PathBuf) -> Self {
        let viewers = match std::fs::read_to_string(&path) {
            Ok(s) => serde_json::from_str(&s).unwrap_or_else(|e| {
                eprintln!("[memory] файл повреждён ({e}), начинаю с пустой памяти");
                let _ = std::fs::copy(&path, path.with_extension("json.bak"));
                HashMap::new()
            }),
            Err(_) => HashMap::new(),
        };
        Memory { path: Some(path), viewers, dirty: false }
    }

    fn key(user: &str) -> String {
        user.trim().trim_start_matches('@').to_lowercase()
    }

    pub fn get(&self, user: &str) -> Option<&ViewerMemo> {
        self.viewers.get(&Self::key(user))
    }

    pub fn len(&self) -> usize {
        self.viewers.len()
    }

    pub fn is_empty(&self) -> bool {
        self.viewers.is_empty()
    }

    /// Запоминает реплику зрителя.
    pub fn observe(&mut self, user: &str, text: &str, now: u64) {
        let key = Self::key(user);
        if key.is_empty() {
            return;
        }
        if !self.viewers.contains_key(&key) && self.viewers.len() >= MAX_VIEWERS {
            if let Some(oldest) = self.viewers.iter().min_by_key(|(_, v)| v.last_seen).map(|(k, _)| k.clone()) {
                self.viewers.remove(&oldest);
            }
        }
        let memo = self.viewers.entry(key).or_insert_with(|| ViewerMemo {
            display_name: user.to_string(),
            first_seen: now,
            ..Default::default()
        });
        memo.display_name = user.to_string();
        memo.messages += 1;
        memo.last_seen = now;
        memo.recent.push(crate::util::truncate_chars(text, 160));
        if memo.recent.len() > MAX_RECENT {
            memo.recent.remove(0);
        }
        for t in detect_topics(text) {
            *memo.topics.entry(t.to_string()).or_insert(0) += 1;
        }
        self.dirty = true;
    }

    /// Зрители, отсортированные по последней активности (свежие первыми).
    pub fn recent_viewers(&self, limit: usize) -> Vec<&ViewerMemo> {
        let mut v: Vec<&ViewerMemo> = self.viewers.values().collect();
        v.sort_by(|a, b| b.last_seen.cmp(&a.last_seen));
        v.truncate(limit);
        v
    }

    /// Удаляет всё о зрителе (по запросу зрителя/стримера).
    pub fn forget(&mut self, user: &str) -> bool {
        let removed = self.viewers.remove(&Self::key(user)).is_some();
        self.dirty |= removed;
        removed
    }

    /// Добавляет заметку; `false`, если зритель неизвестен.
    pub fn add_note(&mut self, user: &str, note: &str) -> bool {
        if let Some(m) = self.viewers.get_mut(&Self::key(user)) {
            m.notes.push(crate::util::truncate_chars(note, 120));
            if m.notes.len() > MAX_NOTES {
                m.notes.remove(0);
            }
            self.dirty = true;
            true
        } else {
            false
        }
    }

    /// Краткая сводка для промпта LLM.
    pub fn summary(&self, user: &str) -> String {
        let Some(m) = self.get(user) else { return "Нова людина, раніше не писала.".into() };
        let mut parts = vec![format!("Писав(ла) {} повідомл.", m.messages)];
        let topics: Vec<&str> = TOPICS
            .iter()
            .filter(|(id, _, _)| m.topic_count(id) > 0)
            .map(|(_, _, note)| *note)
            .collect();
        if !topics.is_empty() {
            parts.push(format!("Помічено: {}.", topics.join("; ")));
        }
        if !m.notes.is_empty() {
            parts.push(format!("Нотатки: {}.", m.notes.join("; ")));
        }
        let recent: Vec<String> = m.recent.iter().rev().take(3).map(|r| format!("«{r}»")).collect();
        if !recent.is_empty() {
            parts.push(format!("Останні репліки: {}.", recent.join(", ")));
        }
        parts.join(" ")
    }

    /// Атомарная запись (tmp + rename). Ничего не делает, если изменений нет.
    pub fn save(&mut self) -> std::io::Result<()> {
        let Some(path) = &self.path else { return Ok(()) };
        if !self.dirty {
            return Ok(());
        }
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_vec_pretty(&self.viewers)?)?;
        std::fs::rename(&tmp, path)?;
        self.dirty = false;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observes_and_summarises() {
        let mut m = Memory::in_memory();
        m.observe("Kurasaki", "Юна, ты нейросеть или за тебя пишут?", 1);
        m.observe("kurasaki", "ну скажи, ты бот?", 2);
        let memo = m.get("KURASAKI").unwrap();
        assert_eq!(memo.messages, 2);
        assert_eq!(memo.topic_count("ai_question"), 2);
        let s = m.summary("kurasaki");
        assert!(s.contains("нейросеть"));
        assert!(s.contains("ты бот"));
        assert!(m.summary("nobody").contains("Нова людина"));
    }

    #[test]
    fn notes_forget_and_listing() {
        let mut m = Memory::in_memory();
        m.observe("a", "привет", 1);
        m.observe("b", "привет", 5);
        assert!(m.add_note("A", "любит пиццу"));
        assert!(!m.add_note("ghost", "x"));
        assert!(m.summary("a").contains("любит пиццу"));
        assert_eq!(m.recent_viewers(10)[0].display_name, "b");
        assert!(m.forget("B"));
        assert!(m.get("b").is_none());
    }

    #[test]
    fn short_keywords_match_whole_words() {
        assert!(!detect_topics("Макс за тебя пишет").contains(&"games"));
        assert!(detect_topics("го в кс, катку").contains(&"games"));
    }

    #[test]
    fn minor_is_remembered() {
        let mut m = Memory::in_memory();
        m.observe("kid", "мне 14 кстати", 1);
        m.observe("kid", "как дела", 2);
        assert_eq!(m.get("kid").unwrap().topic_count("minor"), 1);
        assert!(m.summary("kid").contains("молодше 18"));
    }

    #[test]
    fn recent_is_bounded() {
        let mut m = Memory::in_memory();
        for i in 0..20 {
            m.observe("u", &format!("msg {i}"), i);
        }
        let memo = m.get("u").unwrap();
        assert_eq!(memo.recent.len(), MAX_RECENT);
        assert_eq!(memo.last_message(), Some("msg 19"));
    }

    #[test]
    fn persists_to_disk() {
        let dir = std::env::temp_dir().join(format!("mira-mem-{}", crate::util::now_ms()));
        let path = dir.join("memory.json");
        let mut m = Memory::load(path.clone());
        m.observe("ordep0152", "о, общение, а не дота", 5);
        m.save().unwrap();
        let m2 = Memory::load(path);
        assert_eq!(m2.get("ordep0152").unwrap().topic_count("games"), 1);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn corrupt_file_does_not_crash() {
        let dir = std::env::temp_dir().join(format!("mira-mem-bad-{}", crate::util::now_ms()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("memory.json");
        std::fs::write(&path, "{not json").unwrap();
        let m = Memory::load(path);
        assert!(m.is_empty());
        let _ = std::fs::remove_dir_all(dir);
    }
}
