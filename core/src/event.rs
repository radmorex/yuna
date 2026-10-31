use serde::{Deserialize, Serialize};

/// Единый формат события чата для всех источников.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChatEvent {
    pub platform: String,
    pub user: String,
    pub text: String,
    pub is_admin: bool,
    pub channel_id: String,
    /// Идентификатор исходного сообщения (для ответа-реплая), если платформа его даёт.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message_id: Option<String>,
    /// Бота упомянули напрямую (Discord @mention) — считается обращением к Юне.
    #[serde(default)]
    pub mentioned: bool,
    /// Неизменяемый ID автора (Discord) — ключ для лимитов; имя может меняться.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_id: Option<String>,
    /// Фото от владельца (data URL). В журнал, память и оверлей не попадает.
    #[serde(skip)]
    pub image: Option<String>,
    /// Написано в инкогнито: не попадает в память и контекст следующих запросов к модели.
    #[serde(skip)]
    pub private: bool,
    /// Пришло со слова-активатора: микрофон слышит и колонки, поэтому сообщения людям требуют подтверждения.
    #[serde(skip)]
    pub voice: bool,
}

impl ChatEvent {
    /// Ключ для лимитов: стабильный ID, если платформа его дала, иначе ник.
    pub fn rate_key(&self) -> String {
        match &self.user_id {
            Some(id) => format!("{}:{id}", self.platform),
            None => format!("{}:{}", self.platform, self.user.to_lowercase()),
        }
    }

    pub fn new(platform: &str, user: &str, text: &str, is_admin: bool, channel_id: &str) -> Self {
        ChatEvent {
            platform: platform.into(),
            user: user.into(),
            text: text.into(),
            is_admin,
            channel_id: channel_id.into(),
            message_id: None,
            mentioned: false,
            user_id: None,
            image: None,
            private: false,
            voice: false,
        }
    }
}

/// Разрешённое фото: data URL JPEG, PNG или WebP в base64, не больше `MAX_IMAGE_BYTES`.
pub const MAX_IMAGE_BYTES: usize = 5_000_000;

/// Разбирает data URL на тип и base64. Всё остальное отклоняется.
pub fn parse_image(url: &str) -> Option<(&str, &str)> {
    let rest = url.strip_prefix("data:")?;
    let (mime, b64) = rest.split_once(";base64,")?;
    let ok_mime = matches!(mime, "image/jpeg" | "image/png" | "image/webp");
    let ok_data = !b64.is_empty() && b64.len() <= MAX_IMAGE_BYTES && b64.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'+' | b'/' | b'='));
    (ok_mime && ok_data).then_some((mime, b64))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Emotion {
    Neutral,
    Smug,
    Surprised,
    Angry,
    Laugh,
}

impl Emotion {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "neutral" | "нейтрально" | "нейтральная" => Some(Emotion::Neutral),
            "smug" | "ухмылка" | "дерзко" => Some(Emotion::Smug),
            "surprised" | "удивление" | "удивлена" => Some(Emotion::Surprised),
            "angry" | "злость" | "злая" => Some(Emotion::Angry),
            "laugh" | "смех" | "смеётся" | "смеется" => Some(Emotion::Laugh),
            _ => None,
        }
    }
    pub fn as_str(&self) -> &'static str {
        match self {
            Emotion::Neutral => "neutral",
            Emotion::Smug => "smug",
            Emotion::Surprised => "surprised",
            Emotion::Angry => "angry",
            Emotion::Laugh => "laugh",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Outfit {
    Hoodie,
    Dress,
}

impl Outfit {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "hoodie" | "худи" | "худі" | "худик" | "толстовка" => Some(Outfit::Hoodie),
            "dress" | "платье" | "платьице" | "сукня" | "сукню" => Some(Outfit::Dress),
            _ => None,
        }
    }
    pub fn as_str(&self) -> &'static str {
        match self {
            Outfit::Hoodie => "hoodie",
            Outfit::Dress => "dress",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_json_shape() {
        let e = ChatEvent::new("twitch", "kurasaki", "Юна, привет", true, "#chan");
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["platform"], "twitch");
        assert_eq!(v["isAdmin"], true);
        assert_eq!(v["channelId"], "#chan");
    }

    #[test]
    fn image_validation() {
        assert_eq!(parse_image("data:image/jpeg;base64,QUJD"), Some(("image/jpeg", "QUJD")));
        assert!(parse_image("https://evil.example/x.jpg").is_none());
        assert!(parse_image("data:text/html;base64,QUJD").is_none());
        assert!(parse_image("data:image/svg+xml;base64,QUJD").is_none());
        assert!(parse_image("data:image/png;base64,не base64").is_none());
        assert!(parse_image(&format!("data:image/png;base64,{}", "A".repeat(MAX_IMAGE_BYTES + 1))).is_none());
    }

    #[test]
    fn whitelists() {
        assert_eq!(Emotion::parse("SMUG"), Some(Emotion::Smug));
        assert_eq!(Emotion::parse("evil"), None);
        assert_eq!(Outfit::parse("платье"), Some(Outfit::Dress));
        assert_eq!(Outfit::parse("bikini"), None);
    }
}
