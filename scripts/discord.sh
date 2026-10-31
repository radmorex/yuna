#!/usr/bin/env bash
# Запуск Discord-бота (Python). Окружение .venv-py создаётся при первом запуске.
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv-py/bin/python ]; then
  command -v uv >/dev/null || { echo "Нужен uv: brew install uv"; exit 1; }
  uv venv .venv-py --python 3.12
  uv pip install --python .venv-py/bin/python -r discord_bot/requirements.txt
fi
exec .venv-py/bin/python -m discord_bot.bot
