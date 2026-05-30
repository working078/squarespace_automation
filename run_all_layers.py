#!/usr/bin/env python3
"""Run test layers 1 → 2 → 3 using .env.test (loads SQ_EMAIL/SQ_PASSWORD if set)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_TEST = ROOT / ".env.test"
PY = ROOT / ".venv" / "bin" / "python"
if not PY.exists():
    PY = Path(sys.executable)


def load_env_test() -> dict[str, str]:
    if not ENV_TEST.exists():
        print("Missing .env.test — copy from .env.test.example")
        sys.exit(1)
    env: dict[str, str] = {}
    for line in ENV_TEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def run_step(name: str, extra: dict[str, str], *args: str) -> int:
    env = os.environ.copy()
    env.update(load_env_test())
    env.update(extra)
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
    cmd = [str(PY), str(ROOT / "automation.py"), *args]
    return subprocess.call(cmd, cwd=ROOT, env=env)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-layer", type=int, default=1, choices=(1, 2, 3))
    args = parser.parse_args()

    base = load_env_test()
    if not base.get("SPREADSHEET_ID"):
        print("Set SPREADSHEET_ID in .env.test")
        return 1

    if args.from_layer >= 2 and not Path(ROOT / "auth.json").exists():
        print(
            "Layers 2–3 need a valid Squarespace session.\n"
            "  Option A: GitHub → Settings → Secrets → AUTH_JSON_BASE64 → copy value\n"
            "           → paste one line into auth_github.txt → python restore_auth.py\n"
            "  Option B: python generate_session.py  (log in in the browser, press Enter)"
        )
        return 1

    steps = [
        ("Layer 1 — sheet + image (no website)", {"DRY_RUN": "1", "DRY_RUN_SKIP_BROWSER": "1"}, ["--offline"]),
        (
            "Layer 2 — fill live editor, no publish",
            {
                "DRY_RUN": "1",
                "ALLOW_PRODUCTION_SITE": "1",
                "HEADLESS": "false",
                "TEST_ROW_LIMIT": base.get("TEST_ROW_LIMIT", "1"),
            },
            [],
        ),
        (
            "Layer 3 — publish one post to live site (test sheet row)",
            {
                "TEST_ROW_LIMIT": base.get("TEST_ROW_LIMIT", "1"),
                "ALLOW_LIVE_LAYER3": "1",
                "HEADLESS": "false",
            },
            [],
        ),
    ]

    steps = [s for i, s in enumerate(steps, start=1) if i >= args.from_layer]

    for name, extra, cmd_args in steps:
        code = run_step(name, extra, *cmd_args)
        if code != 0:
            print(f"\nStopped: {name} failed (exit {code})")
            if "Layer 2" in name:
                print(
                    "If login failed: add auth_github.txt from GitHub secret AUTH_JSON_BASE64,\n"
                    "  or run: python generate_session.py"
                )
            return code

    print("\nAll layers finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
