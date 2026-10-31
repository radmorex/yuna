#!/usr/bin/env bash
# Демо без ключей: мок-чат + mock-мозг. Голос: macOS `say` (если есть), иначе без звука.
set -e
source "$(dirname "$0")/_env.sh"
build_core
export SOURCES=mock LLM_PROVIDER=mock
export MOCK_CHAT_INTERVAL_MS="${MOCK_CHAT_INTERVAL_MS:-3400}"
export MEMORY_FILE="${MEMORY_FILE_DEMO:-data/demo-memory.json}"
if [ -z "$DEMO_TTS" ]; then
  DEMO_TTS=$(pick_tts)
fi
export TTS_PROVIDER="$DEMO_TTS"
export PORT="${PORT:-8787}" HOST=127.0.0.1
rm -f "$MEMORY_FILE"
URL="http://127.0.0.1:$PORT/overlay.html"
echo "▶ Юна (демо): $URL   панель: http://127.0.0.1:$PORT/control.html   TTS=$TTS_PROVIDER"
if [ -z "$NO_OPEN" ] && command -v open >/dev/null 2>&1; then (sleep 1.5; open "$URL?bg=1") & fi
exec ./core/target/release/mira
