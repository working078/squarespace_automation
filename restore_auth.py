#!/usr/bin/env python3
"""Restore auth.json from auth_github.txt or AUTH_JSON_BASE64 env (GitHub secret export)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from session_utils import decode_github_secret_payload

ROOT = Path(__file__).resolve().parent
AUTH_PATH = ROOT / "auth.json"
CANDIDATES = (
    ROOT / "auth_github.txt",
    ROOT / "auth_b64.txt",
)


def main() -> int:
    payload = os.getenv("AUTH_JSON_BASE64", "").strip()
    if not payload:
        for path in CANDIDATES:
            if path.exists():
                payload = path.read_text(encoding="ascii").strip()
                print(f"Loaded base64 from {path.name}")
                break
    if not payload:
        print(
            "No session found. Either:\n"
            "  • Paste GitHub secret AUTH_JSON_BASE64 into auth_github.txt (one line), or\n"
            "  • export AUTH_JSON_BASE64='...' && python restore_auth.py\n"
            "  • python generate_session.py (log in in browser)"
        )
        return 1
    raw = decode_github_secret_payload(payload)
    AUTH_PATH.write_bytes(raw)
    print(f"Wrote auth.json ({len(raw)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
