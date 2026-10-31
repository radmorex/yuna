#!/usr/bin/env bash
# Все тесты: Rust (cargo test) + Python (unittest).
set -e
source "$(dirname "$0")/_env.sh"
echo "── Rust ──"; cargo test --manifest-path core/Cargo.toml -q
echo "── Python ──"; python3 -m unittest discover -s tests_py
echo "✔ все тесты зелёные"
