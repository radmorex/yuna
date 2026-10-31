//! Источники чата. Все отдают `ChatEvent` в общий канал хаба.
//! Discord подключается через WebSocket-мост `/bridge` (см. `server.rs` и `discord_bot/`).
pub mod mock;
pub mod twitch;
