# Общие настройки скриптов: находим cargo (rustup-тулчейн без глобального PATH тоже подходит).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if ! command -v cargo >/dev/null 2>&1; then
  for d in "$HOME/.cargo/bin" "$HOME"/.rustup/toolchains/stable-*/bin; do
    [ -x "$d/cargo" ] && export PATH="$d:$PATH" && break
  done
fi
command -v cargo >/dev/null 2>&1 || { echo "Не найден cargo. Установите Rust: https://rustup.rs"; exit 1; }
# Голос по выбору владельца: Svetlana, затем локальный Piper, затем системный macOS, иначе без звука.
pick_tts() {
  if [ -x .venv-tts/bin/edge-tts ]; then echo edge
  elif [ -s .cache/piper/ru_RU-irina-medium.onnx ]; then echo piper
  elif command -v say >/dev/null 2>&1; then echo say
  else echo none; fi
}
# Запускает ядро; при голосе rvc рядом с ним сайдкар. Живут и умирают вместе:
# без сайдкара ядро молча перешло бы на голос Piper.
run_core() {
  CORE_PID=""; RVC_PID=""
  trap 'kill $CORE_PID $RVC_PID 2>/dev/null' EXIT INT TERM
  if [ "$TTS_PROVIDER" = "rvc" ] && [ -x .venv-rvc/bin/python ]; then
    nice -n 5 .venv-rvc/bin/python tools/rvc_server.py &
    RVC_PID=$!
  fi
  ./core/target/release/mira &
  CORE_PID=$!
  # bash 3.2 на macOS не умеет wait -n, поэтому проверяем оба процесса сами.
  while kill -0 "$CORE_PID" 2>/dev/null && { [ -z "$RVC_PID" ] || kill -0 "$RVC_PID" 2>/dev/null; }; do
    sleep 2
  done
  echo "Один из процессов завершился, останавливаю остальные"
}
build_core() {
  cargo build --release --manifest-path core/Cargo.toml -q || exit 1
}
