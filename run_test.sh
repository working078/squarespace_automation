#!/usr/bin/env bash
# Safe test runner — loads .env.test and delegates to run_tests.py
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env.test ]]; then
  echo "Create .env.test from the example first:"
  echo "  cp .env.test.example .env.test"
  exit 1
fi

LAYER="${1:-1}"
LIMIT="${2:-1}"

python3 -m venv .venv 2>/dev/null || true
if [[ -f .venv/bin/pip ]]; then
  .venv/bin/pip install -q -r requirements.txt
fi

PY="${PY:-python3}"
if [[ -f .venv/bin/python ]]; then
  PY=".venv/bin/python"
fi

exec "$PY" run_tests.py --layer "$LAYER" --limit "$LIMIT"
