use mira_core::brain::{llm::Llm, memory::Memory, Brain};
use mira_core::config::{load_env, Config};
use mira_core::pipeline::{self, Engine, Hub};
use mira_core::sources;
use mira_core::tts::Tts;
use std::path::Path;

#[tokio::main]
async fn main() {
    let cfg = Config::from_map(&load_env(Path::new(".env")));
    let (hub, rx) = Hub::new(cfg.clone());
    let (llm, w1) = Llm::from_config(&cfg);
    let (tts, w2) = Tts::from_config(&cfg);
    for w in [w1, w2].into_iter().flatten() {
        hub.log(&format!("[config] {w}"));
    }
    let brain = Brain::new(&cfg, Memory::load(cfg.memory_file.clone()));
    let engine = Engine::new(hub.clone(), brain, llm, tts);
    hub.set_wake(pipeline::wake_on_start());

    hub.log(&format!(
        "Юна запускається: джерела={:?} LLM={} TTS={} пам'ять={}",
        cfg.sources,
        engine.llm.name(),
        engine.tts.name(),
        cfg.memory_file.display()
    ));

    if cfg.has_source("mock") {
        tokio::spawn(sources::mock::run(hub.clone()));
    }
    if cfg.has_source("twitch") {
        tokio::spawn(sources::twitch::run(hub.clone()));
    }
    if cfg.has_source("discord") {
        hub.log("[discord] жду Python-бота на ws://…/bridge (запуск: scripts/discord.sh)");
    }
    let system = mira_core::brain::persona::system_prompt(&cfg.bot_name, cfg.max_reply_chars);
    engine.llm.warm_up(&system).await;
    tokio::spawn(pipeline::run(engine.clone(), rx));
    tokio::spawn(pipeline::memory_saver(engine.clone()));
    tokio::spawn(pipeline::commentator(engine.clone()));

    let addr = format!("{}:{}", cfg.host, cfg.port);
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            hub.log(&format!("Не вдалося зайняти {addr}: {e}. Змініть PORT у .env"));
            std::process::exit(1);
        }
    };
    hub.log(&format!("Оверлей:  http://{addr}/overlay.html"));
    hub.log(&format!("Панель:   http://{addr}/control.html"));
    if cfg.host == "0.0.0.0" && cfg.control_token.is_none() {
        hub.log("ВНИМАНИЕ: HOST=0.0.0.0 без CONTROL_TOKEN — панель доступна из сети. Задайте CONTROL_TOKEN.");
    }

    let saver = engine.clone();
    let shutdown = async move {
        // Окно закрывает ядро через SIGTERM: без этого теряются последние 15 секунд памяти.
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = term.recv() => {}
        }
        let _ = saver.brain.lock().unwrap().memory.save();
        saver.hub.log("Пам'ять збережено. Бувай!");
    };
    if let Err(e) = axum::serve(listener, mira_core::server::router(engine)).with_graceful_shutdown(shutdown).await {
        hub.log(&format!("сервер упал: {e}"));
    }
}
