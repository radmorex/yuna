//! Теги управления в ответе модели: `[emotion:smug]`, `[outfit:dress]`.
use crate::event::{Emotion, Outfit};

#[derive(Debug, PartialEq)]
pub struct Parsed {
    pub text: String,
    pub emotion: Option<Emotion>,
    pub outfit: Option<Outfit>,
}

/// Находит все `[ключ:значение]`, применяет только белый список, а из текста
/// вырезает любые такие теги (включая неизвестные) — их не будет ни в чате, ни в озвучке.
pub fn parse_tags(raw: &str) -> Parsed {
    let mut text = String::with_capacity(raw.len());
    let mut emotion = None;
    let mut outfit = None;
    let mut rest = raw;
    while let Some(start) = rest.find('[') {
        text.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        match after.find(']') {
            // Слабые модели пишут просто «[smug]» — это тоже тег эмоции, в озвучку он попасть не должен.
            Some(end) if Emotion::parse(&after[..end]).is_some() => {
                emotion = Emotion::parse(&after[..end]);
                rest = &after[end + 1..];
            }
            Some(end) if is_tag_body(&after[..end]) => {
                let body = &after[..end];
                let (k, v) = body.split_once(':').unwrap();
                match k.trim().to_lowercase().as_str() {
                    "emotion" | "emo" => emotion = Emotion::parse(v).or(emotion),
                    "outfit" => outfit = Outfit::parse(v).or(outfit),
                    _ => {}
                }
                rest = &after[end + 1..];
            }
            _ => {
                text.push('[');
                rest = after;
            }
        }
    }
    text.push_str(rest);
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let text = text.replace(" ,", ",").replace(" .", ".").replace(" !", "!").replace(" ?", "?");
    Parsed { text, emotion, outfit }
}

fn is_tag_body(body: &str) -> bool {
    let Some((k, v)) = body.split_once(':') else { return false };
    let ok = |s: &str| !s.trim().is_empty() && s.trim().chars().all(|c| c.is_alphanumeric() || c == '_' || c == '-');
    body.len() <= 40 && ok(k) && ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_and_strips() {
        let p = parse_tags("[emotion:smug] Ну конечно. [outfit:dress]");
        assert_eq!(p.text, "Ну конечно.");
        assert_eq!(p.emotion, Some(Emotion::Smug));
        assert_eq!(p.outfit, Some(Outfit::Dress));
    }

    #[test]
    fn unknown_tags_stripped_not_applied() {
        let p = parse_tags("Привет [emotion:evil] [cmd:rm] [outfit:bikini]!");
        assert_eq!(p.text, "Привет!");
        assert_eq!(p.emotion, None);
        assert_eq!(p.outfit, None);
    }

    #[test]
    fn bare_emotion_tag_is_recognised() {
        let p = parse_tags("[smug] Ну конечно.");
        assert_eq!(p.text, "Ну конечно.");
        assert_eq!(p.emotion, Some(Emotion::Smug));
    }

    #[test]
    fn ordinary_brackets_kept() {
        let p = parse_tags("массив [1, 2] и [ну такое]");
        assert_eq!(p.text, "массив [1, 2] и [ну такое]");
    }
}
