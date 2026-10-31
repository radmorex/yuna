#!/usr/bin/env bash
# Необов'язкові пакети: bash scripts/setup.sh tools | voices | rvc | model | all
set -e
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Потрібен uv: brew install uv"; exit 1; }

# Інструменти OSINT: окреме оточення всередині vendor/osint-kit.
tools() {
  [ -x vendor/osint-kit/.venv/bin/python ] || uv venv vendor/osint-kit/.venv --python 3.12
  # Фиксированные версии: новые сборки уже ломали установку.
  uv pip install --python vendor/osint-kit/.venv/bin/python -r vendor/osint-kit/requirements.lock
  uv pip install --python vendor/osint-kit/.venv/bin/python --no-deps -e vendor/osint-kit
  # Своє сховище справ, щоб не конфліктувати з окремо встановленим osint-kit.
  if [ ! -f vendor/osint-kit/config.toml ]; then
    mkdir -p "$HOME/Library/Application Support/yuna"
    printf '[general]\nvault_image = "~/Library/Application Support/yuna/cases.sparsebundle"\n' > vendor/osint-kit/config.toml
  fi
  echo "✔ Інструменти OSINT встановлено"
}

# Голоси edge-tts і Piper.
voices() {
  bash scripts/voice.sh
}

# Моделі RVC лежать у приватному релізі: файли завеликі для git.
rvc() {
  command -v gh >/dev/null || { echo "Потрібен gh: brew install gh"; exit 1; }
  mkdir -p voices
  gh release download voices-v1 --repo radmorex/yuna --pattern 'voices-rvc.zip' --dir voices --clobber
  unzip -oq voices/voices-rvc.zip -d voices
  rm voices/voices-rvc.zip
  [ -x .venv-rvc/bin/python ] || uv venv .venv-rvc --python 3.10
  uv pip install --python .venv-rvc/bin/python -r tools/requirements-rvc.txt
  echo "✔ Голоси RVC встановлено. У .env: TTS_PROVIDER=rvc, RVC_VOICE=anna або aria"
}

# Локальна модель: ключ не потрібен, важить близько 5 ГБ.
model() {
  command -v ollama >/dev/null || { echo "Потрібна Ollama: brew install ollama"; exit 1; }
  ollama pull "${OLLAMA_MODEL:-qwen3:8b}"
  echo "✔ Модель встановлено. У .env: LLM_PROVIDER=ollama"
}

case "${1:-}" in
  tools) tools ;;
  voices) voices ;;
  rvc) rvc ;;
  model) model ;;
  all) tools; voices; rvc; model ;;
  *) echo "Використання: bash scripts/setup.sh tools|voices|rvc|model|all"; exit 1 ;;
esac
