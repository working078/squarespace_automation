"""
Run this ONCE on your local machine to generate auth.json.
Uses a persistent Chrome profile so login survives blank-page / OAuth issues.

Usage:
    .venv/bin/python generate_session.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

from session_utils import slim_file, write_github_secret_file

LOGIN_URL = "https://login.squarespace.com/"
DASHBOARD_URL = "https://account.squarespace.com/config/"
PROFILE_DIR = Path(__file__).resolve().parent / ".sqs_chrome_profile"
AUTH_STATE_PATH = "auth.json"
AUTH_FULL_PATH = "auth_full.json"


def is_logged_in(url: str) -> bool:
    if "account.squarespace.com" in url:
        return "/authorize" not in url and "/login" not in url
    return False


def launch_context(p):
    PROFILE_DIR.mkdir(exist_ok=True)
    opts = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": False,
        "viewport": {"width": 1400, "height": 900},
        "locale": "en-AU",
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        print("Opening Google Chrome (persistent profile — login is remembered)...")
        return p.chromium.launch_persistent_context(channel="chrome", **opts)
    except Exception as e:
        print(f"System Chrome unavailable ({e}), using Chromium...")
        return p.chromium.launch_persistent_context(**opts)


print("=" * 60)
print("Squarespace login — save session for automation")
print("=" * 60)

with sync_playwright() as p:
    context = launch_context(p)
    page = context.pages[0] if context.pages else context.new_page()

    print(f"\nOpening {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=120000)

    print("\n" + "=" * 60)
    print("IN THE BROWSER WINDOW:")
    print("  • If the page is WHITE/BLANK → address bar → https://login.squarespace.com")
    print("  • Log in (email, password, 2FA)")
    print("  • You must reach the dashboard (site list), NOT stay on a login URL")
    print("  • Optional: open https://account.squarespace.com/config/")
    print("=" * 60)
    input("\nPress ENTER only when you see the Squarespace dashboard...")

    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(4000)
    current = page.url
    print(f"\nCurrent URL: {current[:100]}...")

    if not is_logged_in(current):
        page.screenshot(path="login_not_complete.png")
        print("\nStill not logged in.")
        print("The session was NOT saved.")
        print("Try again: log in fully, then press Enter only on the dashboard.")
        context.close()
        raise SystemExit(1)

    context.storage_state(path=AUTH_FULL_PATH)
    print(f"\nFull session saved to {AUTH_FULL_PATH}")

    before, after = slim_file(AUTH_FULL_PATH, AUTH_STATE_PATH)
    _, fmt, secret_len = write_github_secret_file(AUTH_STATE_PATH, "auth_github.txt")
    context.close()

print(f"\nSlim auth.json: {before:,} -> {after:,} bytes")
print(f"GitHub secret payload: {secret_len:,} chars ({fmt}) -> auth_github.txt")
print("\nUpdate GitHub: Settings -> Secrets -> AUTH_JSON_BASE64 (paste auth_github.txt)")
print("Then run: ./setup_local_auth.sh")
