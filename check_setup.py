#!/usr/bin/env python3
"""Verify local test setup before running run_tests.py. No secrets printed."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTION_SHEET = "18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts"
PRODUCTION_SITE = "https://coconut-radish-an89.squarespace.com"


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def warn(msg: str) -> None:
    print(f"  !!  {msg}")


def fail(msg: str) -> None:
    print(f"  XX  {msg}")


def load_env_test() -> dict[str, str]:
    path = ROOT / ".env.test"
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    print("Squarespace automation — setup check\n")
    issues = 0

    # Google credentials
    creds_path = ROOT / "credentials.json"
    if os.getenv("GOOGLE_CREDENTIALS"):
        ok("GOOGLE_CREDENTIALS environment variable is set")
        try:
            data = json.loads(os.environ["GOOGLE_CREDENTIALS"])
            email = data.get("client_email", "")
            if email:
                ok(f"Service account email: {email}")
                print("       Share your TEST Google Sheet with this address (Editor).")
        except json.JSONDecodeError:
            fail("GOOGLE_CREDENTIALS is not valid JSON")
            issues += 1
    elif creds_path.exists():
        ok(f"Found {creds_path.name}")
        try:
            data = json.loads(creds_path.read_text(encoding="utf-8"))
            email = data.get("client_email", "")
            if email:
                ok(f"Service account email: {email}")
                print("       Share your TEST Google Sheet with this address (Editor).")
            else:
                warn("credentials.json has no client_email field")
        except (json.JSONDecodeError, OSError) as e:
            fail(f"Could not read credentials.json: {e}")
            issues += 1
    else:
        fail("Missing credentials.json (ask your friend for the Google service account JSON)")
        issues += 1

    # Test config
    env_test = load_env_test()
    if not env_test:
        fail("Missing .env.test — run: cp .env.test.example .env.test")
        issues += 1
    else:
        ok("Found .env.test")
        sheet = env_test.get("SPREADSHEET_ID", "").strip()
        if not sheet or "your_test" in sheet:
            fail("Set SPREADSHEET_ID in .env.test (test copy of the sheet, not placeholder)")
            issues += 1
        elif sheet == PRODUCTION_SHEET:
            warn("SPREADSHEET_ID is the LIVE production sheet — use a copy for safe tests")
        else:
            ok(f"Test spreadsheet ID configured ({sheet[:8]}...)")

        base = env_test.get("BASE_URL", "").strip().rstrip("/")
        if not base or "your-duplicate" in base:
            warn("BASE_URL not set — needed for layer 2+ (duplicate Squarespace site)")
        elif base == PRODUCTION_SITE.rstrip("/"):
            warn("BASE_URL points at LIVE site — use duplicate site URL for browser tests")
        else:
            ok(f"Test site URL configured")

    # Squarespace session (layers 2+)
    if (ROOT / "auth.json").exists():
        ok("auth.json present (Squarespace session for browser tests)")
    else:
        warn("No auth.json — run generate_session.py after credentials (layer 2+)")

    # Dependencies
    try:
        import google.oauth2  # noqa: F401
        ok("Python packages installed (google-auth)")
    except ImportError:
        fail("Run: pip install -r requirements.txt")
        issues += 1

    print()
    if issues:
        print(f"Fix {issues} item(s) above, then run: ./run_test.sh 1")
        return 1
    print("Ready for layer 1: ./run_test.sh 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
