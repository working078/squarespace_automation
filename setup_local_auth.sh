#!/usr/bin/env bash
# One-time Squarespace login → auth.json → run layers 2–3
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || PY=python3

if [ -f auth_github.txt ]; then
  echo "Restoring session from auth_github.txt..."
  "$PY" restore_auth.py
elif [ -f auth.json ]; then
  echo "Using existing auth.json"
else
  echo "Opening browser — log in to Squarespace, then press ENTER in this terminal."
  "$PY" generate_session.py
fi

"$PY" -c "
from playwright.sync_api import sync_playwright
import time, sys
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    ctx=b.new_context(storage_state='auth.json')
    page=ctx.new_page()
    page.goto('https://account.squarespace.com/config/', wait_until='domcontentloaded')
    time.sleep(5)
    u=page.url
    b.close()
    if '/login' in u or '/authorize' in u:
        print('ERROR: auth.json is not a valid login session.')
        sys.exit(1)
    print('Session OK.')
"

echo "Running layers 2 and 3..."
"$PY" run_all_layers.py --from-layer 2
