#!/usr/bin/env bash
# Разговор один на один: без фейкового чата и кулдаунов. Мозг берётся из .env (без ключа — шаблоны).
set -e
source "$(dirname "$0")/_env.sh"
build_core
# Мозг: TALK_LLM, иначе из .env; если там ничего нет — локальный Qwen через Ollama.
# Подписку Claude не трогаем без явного TALK_LLM=claude.
if [ -n "$TALK_LLM" ]; then
  export LLM_PROVIDER="$TALK_LLM"
elif ! grep -qs '^LLM_PROVIDER=' .env; then
  export LLM_PROVIDER=ollama
fi
export SOURCES=none HOST=127.0.0.1 PORT="${PORT:-8787}"
export REPLY_COOLDOWN_SECS=0 USER_COOLDOWN_SECS=0 USER_MAX_PER_MINUTE=60
export MEMORY_FILE="${MEMORY_FILE_TALK:-data/talk-memory.json}"
if [ -z "$TALK_TTS" ]; then
  TALK_TTS=$(pick_tts)
fi
export TTS_PROVIDER="$TALK_TTS"
URL="http://127.0.0.1:$PORT/talk.html"
echo "▶ Разговор с Юной: $URL   TTS=$TTS_PROVIDER"
if [ -z "$NO_OPEN" ] && command -v open >/dev/null 2>&1; then (sleep 1.5; open "$URL") & fi
run_core
