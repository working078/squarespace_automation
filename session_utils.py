"""Trim Playwright storage state so it fits GitHub Secrets (64 KiB max)."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

# GitHub Actions secret value limit
GITHUB_SECRET_MAX = 64 * 1024

SQUARESPACE_SUFFIXES = ("squarespace.com", "sqsp.net")


def is_squarespace_cookie(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return any(d == suffix or d.endswith("." + suffix) for suffix in SQUARESPACE_SUFFIXES)


def slim_storage_state(state: dict) -> dict:
    """Keep Squarespace cookies only; drop localStorage (large, rarely needed)."""
    cookies = [
        c
        for c in state.get("cookies", [])
        if is_squarespace_cookie(c.get("domain", ""))
    ]
    return {"cookies": cookies, "origins": []}


def slim_file(
    source: str | Path = "auth.json",
    dest: str | Path = "auth.json",
) -> tuple[int, int]:
    """Load full session, write slim JSON. Returns (bytes_before, bytes_after)."""
    source, dest = Path(source), Path(dest)
    with source.open(encoding="utf-8") as f:
        state = json.load(f)
    before = len(json.dumps(state, separators=(",", ":")).encode())
    slim = slim_storage_state(state)
    slim_bytes = json.dumps(slim, separators=(",", ":")).encode("utf-8")
    dest.write_bytes(slim_bytes)
    return before, len(slim_bytes)


def encode_for_github_secret(json_bytes: bytes) -> tuple[str, str]:
    """Return (base64 payload, 'plain' | 'gzip') under GitHub's size limit."""
    plain_b64 = base64.b64encode(json_bytes).decode("ascii")
    if len(plain_b64) <= GITHUB_SECRET_MAX:
        return plain_b64, "plain"

    gz = gzip.compress(json_bytes, compresslevel=9)
    gz_b64 = base64.b64encode(gz).decode("ascii")
    if len(gz_b64) <= GITHUB_SECRET_MAX:
        return gz_b64, "gzip"

    raise ValueError(
        f"Encoded session is {len(gz_b64)} chars (limit {GITHUB_SECRET_MAX}). "
        "Re-login with generate_session.py, or skip AUTH_JSON_BASE64 and use "
        "Actions cache (squarespace-auth-session) after a successful CI run."
    )


def decode_github_secret_payload(payload_b64: str) -> bytes:
    raw = base64.b64decode(payload_b64.strip())
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def write_github_secret_file(
    auth_path: str | Path = "auth.json",
    out_path: str | Path = "auth_github.txt",
) -> tuple[str, str, int]:
    """Slim auth.json and write one-line secret payload to auth_github.txt."""
    auth_path = Path(auth_path)
    slim_file(auth_path, auth_path)
    json_bytes = auth_path.read_bytes()
    payload, fmt = encode_for_github_secret(json_bytes)
    Path(out_path).write_text(payload, encoding="ascii")
    return payload, fmt, len(payload)
