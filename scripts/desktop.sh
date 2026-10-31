#!/usr/bin/env bash
# Компаньон на рабочем столе: ядро, слушатель звука ПК и прозрачное окно. Закрыли окно — закрылось всё.
set -e
source "$(dirname "$0")/_env.sh"
build_core
[ -x .venv-desktop/bin/python ] || {
  uv venv .venv-desktop --python 3.12
  uv pip install --python .venv-desktop/bin/python 'pywebview==6.2.1' 'faster-whisper==1.2.1'
}
mkdir -p .cache/bin
if [ ! -x .cache/bin/syscap ] || [ tools/syscap.swift -nt .cache/bin/syscap ]; then
  swiftc -O tools/syscap.swift -o .cache/bin/syscap
fi
if ! grep -qs '^LLM_PROVIDER=' .env; then export LLM_PROVIDER=ollama; fi
export SOURCES=none HOST=127.0.0.1 PORT="${PORT:-8787}"
export REPLY_COOLDOWN_SECS=0 USER_COOLDOWN_SECS=0 USER_MAX_PER_MINUTE=60
export MEMORY_FILE="${MEMORY_FILE_TALK:-data/talk-memory.json}"
export TTS_PROVIDER="${TALK_TTS:-$(pick_tts)}"
export CORE_URL="http://127.0.0.1:$PORT" WAKE_DEFAULT=1
./core/target/release/mira &
CORE_PID=$!
# Микрофон и звук системы слушаем, только если доступ к ПК включён в .env.
LISTEN_PID=""; WAKE_PID=""
if grep -qsE '^PC_CONTROL=[Tt][Rr][Uu][Ee]' .env; then
  nice -n 5 .venv-desktop/bin/python tools/listener.py &
  LISTEN_PID=$!
  nice -n 5 .venv-desktop/bin/python tools/wake.py &
  WAKE_PID=$!
fi
trap 'kill $CORE_PID $LISTEN_PID $WAKE_PID 2>/dev/null' EXIT INT TERM
.venv-desktop/bin/python tools/desktop.py
