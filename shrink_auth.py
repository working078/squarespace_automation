"""
Re-slim an existing auth.json (e.g. after a full Playwright export).

  python shrink_auth.py
  python shrink_auth.py path/to/auth.json
"""

import sys
from pathlib import Path

from session_utils import slim_file, write_github_secret_file

path = Path(sys.argv[1] if len(sys.argv) > 1 else "auth.json")
if not path.exists():
    raise SystemExit(f"Not found: {path}. Run generate_session.py first.")

before, after = slim_file(path, path)
payload, fmt, secret_len = write_github_secret_file(path, "auth_github.txt")
print(f"Slimmed {path.name}: {before:,} -> {after:,} bytes")
print(f"GitHub secret payload: {secret_len:,} chars ({fmt}) -> auth_github.txt")
print("Paste auth_github.txt into Settings -> Secrets -> AUTH_JSON_BASE64")
