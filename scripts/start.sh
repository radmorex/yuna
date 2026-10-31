#!/usr/bin/env bash
# Боевой запуск: всё берётся из .env.
set -e
source "$(dirname "$0")/_env.sh"
[ -f .env ] || { echo "Нет .env — скопируйте .env.example в .env и заполните"; exit 1; }
build_core
exec ./core/target/release/mira
