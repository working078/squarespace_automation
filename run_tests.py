#!/usr/bin/env python3
"""
Safe local testing for Tribe Squarespace automation.

Production sheet and live site are protected by default (see automation.validate_run_safety).

Usage:
  1. Copy .env.test.example → .env.test and fill in your TEST spreadsheet + duplicate site URL.
  2. Share the test spreadsheet with your service account (same as production).
  3. Run layers in order:

     python run_tests.py --layer 1    # sheet + image only (no browser)
     python run_tests.py --layer 2    # browser fills editor, no publish
     python run_tests.py --layer 3    # full publish on TEST site + TEST sheet only

Never use --layer 3 until layers 1–2 pass.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_TEST = ROOT / ".env.test"
PRODUCTION_SHEET = "18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts"
PRODUCTION_SITE = "https://coconut-radish-an89.squarespace.com"


def load_env_test() -> dict[str, str]:
    if not ENV_TEST.exists():
        return {}
    env: dict[str, str] = {}
    for line in ENV_TEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def merge_env(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    out = os.environ.copy()
    out.update(base)
    out.update(extra)
    return out


def require_test_sheet(env: dict[str, str]) -> str:
    sheet = env.get("SPREADSHEET_ID", "").strip()
    if not sheet:
        print(
            "Missing SPREADSHEET_ID in .env.test.\n"
            "  1. In Google Sheets: File → Make a copy of the production spreadsheet\n"
            "  2. Share the copy with your service account email\n"
            "  3. Copy the copy's ID from the URL into .env.test\n"
            f"  (Production ID is blocked for tests: {PRODUCTION_SHEET})"
        )
        sys.exit(1)
    if sheet == PRODUCTION_SHEET and not os.getenv("ALLOW_PRODUCTION_SHEET_READ"):
        print(
            "SPREADSHEET_ID in .env.test must be a COPY of the production sheet, not the live one."
        )
        sys.exit(1)
    return sheet


def require_test_site(env: dict[str, str], *, layer: int) -> str:
    base = env.get("BASE_URL", "").strip().rstrip("/")
    if layer >= 2 and not base:
        print(
            "Missing BASE_URL in .env.test.\n"
            "  Duplicate the Squarespace site (trial/clone) and paste that site's URL."
        )
        sys.exit(1)
    if base and base.rstrip("/") == PRODUCTION_SITE.rstrip("/"):
        print("BASE_URL in .env.test must be your duplicate/test site, not the live Tribe URL.")
        sys.exit(1)
    return base


def run_python(env: dict[str, str], *args: str) -> int:
    cmd = [sys.executable, str(ROOT / "automation.py"), *args]
    print("Running:", " ".join(cmd))
    print("  DRY_RUN =", env.get("DRY_RUN", "(unset)"))
    print("  SPREADSHEET_ID =", env.get("SPREADSHEET_ID", "(production default)"))
    print("  BASE_URL =", env.get("BASE_URL", "(production default)"))
    print()
    return subprocess.call(cmd, cwd=ROOT, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe layered tests (Option D)")
    parser.add_argument(
        "--layer",
        type=int,
        choices=(1, 2, 3),
        required=True,
        help="1=offline, 2=dry-run browser on test site, 3=live publish on test site only",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Max rows to process (default 1)",
    )
    args = parser.parse_args()

    file_env = load_env_test()
    if not file_env and args.layer <= 2:
        print(
            "No .env.test found. Copy .env.test.example → .env.test and configure test resources.\n"
            "  cp .env.test.example .env.test"
        )
        sys.exit(1)

    sheet = require_test_sheet(file_env)
    site = require_test_site(file_env, layer=args.layer)

    extra: dict[str, str] = {
        "SPREADSHEET_ID": sheet,
        "TEST_ROW_LIMIT": str(max(1, args.limit)),
    }
    if site:
        extra["BASE_URL"] = site

    if args.layer == 1:
        extra["DRY_RUN"] = "1"
        extra["DRY_RUN_SKIP_BROWSER"] = "1"
        return run_python(merge_env(file_env, extra), "--offline")

    if args.layer == 2:
        extra["DRY_RUN"] = "1"
        extra["HEADLESS"] = os.getenv("HEADLESS", "false")
        return run_python(merge_env(file_env, extra))

    # Layer 3 — test resources only; never production
    extra.pop("DRY_RUN", None)
    print("=" * 60)
    print("LAYER 3 — will PUBLISH to the TEST Squarespace site and update the TEST sheet.")
    print(f"  Sheet: {sheet}")
    print(f"  Site:  {site}")
    print("=" * 60)
    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return 1
    return run_python(merge_env(file_env, extra))


if __name__ == "__main__":
    raise SystemExit(main())
