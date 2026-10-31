#!/usr/bin/env bash
# Установка osint-kit на macOS (Apple Silicon).
# Повторный запуск безопасен: уже установленное пропускается, зависимости обновляются.
#
#   ./install.sh            — полная установка
#   ./install.sh --no-rust  — без Rust (ядро не собирается, работает Python-фолбэк)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
PY_VERSION="3.12"
BIN_DIR="$HOME/.local/bin"
WITH_RUST=1

for arg in "$@"; do
  case "$arg" in
    --no-rust) WITH_RUST=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "Скрипт рассчитан на macOS."
[[ "$(uname -m)" == "arm64" ]] || warn "Не arm64 ($(uname -m)) — скрипт проверялся только на Apple Silicon."
command -v brew >/dev/null || die "Нужен Homebrew: https://brew.sh"

# --- uv ---------------------------------------------------------------------
if ! command -v uv >/dev/null; then
  say "Устанавливаю uv (brew)"
  brew install uv
fi

# --- системные утилиты (exiftool, ffmpeg, tor) --------------------------------
# tor — маршрут по умолчанию: сайты видят выход Tor, а не IP искателя (программа запускает его сама)
for pkg in exiftool ffmpeg tor; do
  if ! command -v "$pkg" >/dev/null; then
    say "Устанавливаю $pkg (brew)"
    brew install "$pkg"
  fi
done

# --- Rust (для будущего ядра) -------------------------------------------------
if [[ $WITH_RUST -eq 1 ]]; then
  # brew-формула rustup keg-only: бинарники (rustup, cargo, rustc) лежат в её префиксе.
  export PATH="$HOME/.cargo/bin:$PATH"
  if ! command -v cargo >/dev/null || ! cargo --version >/dev/null 2>&1; then
    say "Устанавливаю Rust (rustup, профиль minimal)"
    brew list rustup >/dev/null 2>&1 || brew install rustup
    export PATH="$(brew --prefix rustup)/bin:$PATH"
    rustup set profile minimal
    rustup default stable
  fi
  [[ -d "$(brew --prefix rustup 2>/dev/null)/bin" ]] && export PATH="$(brew --prefix rustup)/bin:$PATH"
  cargo --version >/dev/null 2>&1 || die "cargo не запускается после установки Rust"
fi

# --- venv на Python 3.12 ------------------------------------------------------
say "Готовлю venv на Python $PY_VERSION ($VENV)"
uv python install "$PY_VERSION"
if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))"; then
  rm -rf "$VENV"
  uv venv --python "$PY_VERSION" "$VENV"
fi

say "Ставлю osint-kit и зависимости"
uv pip install --python "$VENV/bin/python" -e "$ROOT[test]"

# --- Rust-ядро (если уже есть в репозитории) ----------------------------------
if [[ $WITH_RUST -eq 1 && -f "$ROOT/core/Cargo.toml" ]]; then
  say "Собираю Rust-ядро (maturin)"
  uv pip install --python "$VENV/bin/python" maturin
  (cd "$ROOT/core" && VIRTUAL_ENV="$VENV" "$VENV/bin/maturin" develop --release --uv) \
    || warn "Rust-ядро не собрано — работает Python-фолбэк"
elif [[ $WITH_RUST -eq 1 ]]; then
  say "Rust-ядра пока нет (core/) — используется Python-фолбэк"
fi

# --- Chromium для web capture -------------------------------------------------
say "Ставлю Chromium для Playwright"
"$VENV/bin/python" -m playwright install chromium

# --- osint в PATH -------------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/osint" "$BIN_DIR/osint"
say "Команда osint: $BIN_DIR/osint"

# --- Fallen Angel.app (окно) ----------------------------------------------------
say "Собираю ~/Applications/Fallen Angel.app"
"$VENV/bin/python" -m osint_kit.app.bundle || warn "Fallen Angel.app не собран — окно можно открыть командой: osint app"

# --- локальный ИИ (Ollama, только на этом компьютере) ----------------------------
command -v ollama >/dev/null || { say "Устанавливаю Ollama (brew)"; brew install ollama; }
pgrep -x ollama >/dev/null || (nohup ollama serve >/dev/null 2>&1 &)
sleep 2
ollama list 2>/dev/null | grep -q "qwen2.5:7b" || { say "Скачиваю модель qwen2.5:7b (~4.7 ГБ)"; ollama pull qwen2.5:7b || warn "модель не скачана — позже: ollama pull qwen2.5:7b"; }
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR нет в PATH. Добавьте в ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

if [[ ! -f "$ROOT/config.toml" ]]; then
  warn "config.toml не найден — ключи API не заданы (это необязательно)."
  warn "Чтобы задать: cp config.example.toml config.toml"
fi

say "Готово. Проверка: osint doctor"
