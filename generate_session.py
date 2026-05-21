"""
Run this ONCE on your local machine to generate auth.json.
Your local IP is not blocked by Squarespace.

Usage:
    pip install playwright playwright-stealth
    playwright install chromium
    python generate_session.py

GitHub secret: paste the contents of auth_github.txt (slim + gzip if needed).
"""

from playwright.sync_api import sync_playwright

from session_utils import slim_file, write_github_secret_file

try:
    from playwright_stealth import stealth_sync

    def apply_stealth(page):
        stealth_sync(page)
except ImportError:
    from playwright_stealth import Stealth

    def apply_stealth(page):
        Stealth().apply_stealth_sync(page)

AUTH_STATE_PATH = "auth.json"
AUTH_FULL_PATH = "auth_full.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1400, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    page = context.new_page()
    apply_stealth(page)

    print("Opening Squarespace login page...")
    page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")

    print("\n" + "=" * 60)
    print("A browser window has opened.")
    print("Please LOG IN manually in that window.")
    print("Complete any 2FA if prompted.")
    print("Wait until you see your Squarespace dashboard.")
    print("=" * 60)
    input("\nPress ENTER here once you are fully logged in and on the dashboard...")

    context.storage_state(path=AUTH_FULL_PATH)
    print(f"\nFull session saved to {AUTH_FULL_PATH} (local backup only)")

    before, after = slim_file(AUTH_FULL_PATH, AUTH_STATE_PATH)
    payload, fmt, secret_len = write_github_secret_file(AUTH_STATE_PATH, "auth_github.txt")
    browser.close()

print(f"\nSlim auth.json: {before:,} -> {after:,} bytes")
print(f"GitHub secret size: {secret_len:,} chars ({fmt})")
print("\n" + "=" * 60)
print("NEXT STEP — GitHub Secret AUTH_JSON_BASE64")
print("=" * 60)
print("Open auth_github.txt, copy the single line, paste into:")
print("  Repo -> Settings -> Secrets and variables -> Actions -> AUTH_JSON_BASE64")
print("=" * 60)
if secret_len > 60000:
    print("WARNING: Still near the 64 KiB limit. If GitHub rejects it, delete")
    print("AUTH_JSON_BASE64 and rely on Actions cache after one successful run.")
