"""
Run this ONCE on your local machine to generate auth.json.
Your local IP is not blocked by Squarespace.

Usage:
    pip install playwright playwright-stealth
    playwright install chromium
    python generate_session.py
"""

import json
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
    def apply_stealth(page): stealth_sync(page)
except ImportError:
    from playwright_stealth import Stealth
    def apply_stealth(page): Stealth().apply_stealth_sync(page)

AUTH_STATE_PATH = "auth.json"

with sync_playwright() as p:
    # Run NON-headless so you can see and interact with the browser
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

    print("\n" + "="*60)
    print("A browser window has opened.")
    print("Please LOG IN manually in that window.")
    print("Complete any 2FA if prompted.")
    print("Wait until you see your Squarespace dashboard.")
    print("="*60)
    input("\nPress ENTER here once you are fully logged in and on the dashboard...")

    # Save the authenticated session
    context.storage_state(path=AUTH_STATE_PATH)
    print(f"\nSession saved to {AUTH_STATE_PATH}")
    browser.close()

# Encode to base64 for GitHub Secret
import base64
with open(AUTH_STATE_PATH, "rb") as f:
    encoded = base64.b64encode(f.read()).decode()

print("\n" + "="*60)
print("NEXT STEP — Add this as GitHub Secret AUTH_JSON_BASE64:")
print("="*60)
print(f"\n{encoded}\n")
print("="*60)
print("Copy everything between the lines above and paste it")
print("into GitHub → Settings → Secrets → AUTH_JSON_BASE64")
