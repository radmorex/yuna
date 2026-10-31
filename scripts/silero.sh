#!/usr/bin/env bash
# Запуск Silero-сайдкара. Первый запуск ставит torch в .venv-silero (см. tools/SILERO.md).
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv-silero/bin/python ]; then
  command -v uv >/dev/null || { echo "Нужен uv: brew install uv"; exit 1; }
  uv venv .venv-silero --python 3.12
  uv pip install --python .venv-silero/bin/python -r tools/requirements-silero.txt
fi
exec .venv-silero/bin/python tools/silero_server.py "$@"
