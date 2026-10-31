#!/usr/bin/env bash
# Установка нейроголоса: edge-tts в локальное окружение .venv-tts.
set -e
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Нужен uv: brew install uv"; exit 1; }
[ -x .venv-tts/bin/python ] || uv venv .venv-tts --python 3.12
uv pip install --python .venv-tts/bin/python 'edge-tts==7.3.1' 'piper-tts==1.8.0'
mkdir -p .cache/piper
for f in ru_RU-irina-medium.onnx ru_RU-irina-medium.onnx.json; do
  [ -s ".cache/piper/$f" ] || curl -fL -o ".cache/piper/$f" "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/$f"
done
echo "✔ Голос установлен. В .env: TTS_PROVIDER=piper"
