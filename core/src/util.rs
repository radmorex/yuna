use std::time::{SystemTime, UNIX_EPOCH};

/// Текущее время в миллисекундах (unix).
pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Маскирует секрет для логов: `abc***yz`. Короткие секреты скрываются полностью.
pub fn mask(secret: &str) -> String {
    let chars: Vec<char> = secret.chars().collect();
    if chars.len() <= 8 {
        return "***".into();
    }
    let head: String = chars[..3].iter().collect();
    let tail: String = chars[chars.len() - 2..].iter().collect();
    format!("{head}***{tail}")
}

/// Заменяет все вхождения известных секретов в строке лога.
pub fn redact(line: &str, secrets: &[String]) -> String {
    let mut out = line.to_string();
    for s in secrets.iter().filter(|s| s.len() >= 4) {
        out = out.replace(s.as_str(), &mask(s));
    }
    out
}

/// Маленький детерминированный ГПСЧ (xorshift64*), чтобы не тянуть `rand`.
#[derive(Clone, Debug)]
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed.max(1))
    }
    pub fn from_time() -> Self {
        Rng::new(now_ms() ^ 0x9E37_79B9_7F4A_7C15)
    }
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    /// Число в диапазоне [0, n).
    pub fn below(&mut self, n: usize) -> usize {
        if n == 0 { 0 } else { (self.next_u64() % n as u64) as usize }
    }
    pub fn chance(&mut self, p: f64) -> bool {
        (self.next_u64() % 10_000) as f64 / 10_000.0 < p
    }
    pub fn pick<'a, T>(&mut self, items: &'a [T]) -> &'a T {
        &items[self.below(items.len())]
    }
}

/// Стабильный цвет ника (как в Twitch-чате), палитра читаема на тёмно-фиолетовом.
pub fn nick_color(nick: &str) -> &'static str {
    const PALETTE: [&str; 10] = [
        "#ff7a9a", "#7ad7ff", "#ffd36e", "#8cff9e", "#c59bff",
        "#ff9f5a", "#5affd8", "#ff6bd6", "#b6ff5a", "#9bb5ff",
    ];
    let mut h: u32 = 2166136261;
    for b in nick.to_lowercase().bytes() {
        h ^= b as u32;
        h = h.wrapping_mul(16777619);
    }
    PALETTE[(h % PALETTE.len() as u32) as usize]
}

pub fn capitalize_first(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).collect(),
        None => String::new(),
    }
}

/// Обрезает строку по символам (не байтам).
pub fn truncate_chars(s: &str, max: usize) -> String {
    s.chars().take(max).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mask_hides_middle() {
        assert_eq!(mask("oauth:abcdefghijk"), "oau***jk");
        assert_eq!(mask("short"), "***");
    }

    #[test]
    fn redact_replaces_secrets() {
        let line = "connecting with token oauth:supersecret123";
        let out = redact(line, &["oauth:supersecret123".into()]);
        assert!(!out.contains("supersecret"));
    }

    #[test]
    fn rng_is_deterministic() {
        let mut a = Rng::new(42);
        let mut b = Rng::new(42);
        assert_eq!(a.next_u64(), b.next_u64());
        assert!(a.below(5) < 5);
    }

    #[test]
    fn nick_color_stable() {
        assert_eq!(nick_color("Kurasaki"), nick_color("kurasaki"));
    }
}
