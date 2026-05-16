import os
import json
import time
import requests
import random
import urllib.parse
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# --- CONFIGURATION ---
SPREADSHEET_ID = '18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
# Verified internal subdomain URL
TARGET_URL = "https://coconut-radish-an89.squarespace.com/config/pages/6a00f5fd27ce801ca25aa32e"
BOOKING_LINK = "https://forms.clickup.com/90161562352/f/2kz0rgqg-676/WM5FMNFXZQWBKHRIBF"
SCHEDULE_TIME = "07:00 AM"
AUTH_STATE_PATH = 'auth.json'

def get_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if creds_json:
        return service_account.Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    return service_account.Credentials.from_service_account_file('credentials.json', scopes=SCOPES)

def update_sheet_status(service, row_index, status):
    range_name = f"Sheet1!D{row_index+2}"
    body = {'values': [[status]]}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=range_name,
        valueInputOption="USER_ENTERED", body=body).execute()

def generate_image(prompt, filename="blog_image.jpg"):
    print(f"Generating image for: {prompt[:50]}...")
    seed = random.randint(1, 1000000)
    encoded_prompt = urllib.parse.quote(f"Professional Australian transport logistics, {prompt}")
    url = f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            return os.path.abspath(filename)
    except Exception as e:
        print(f"Image generation failed: {e}")
    return None

def run_automation():
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    result = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="Sheet1!A2:D").execute()
    rows = result.get('values', [])
    
    today_str = datetime.now().strftime("%d/%m/%y")
    EMAIL = os.getenv("SQ_EMAIL")
    PASSWORD = os.getenv("SQ_PASSWORD")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # Determine Context (Session vs Fresh)
        context_args = {
            "viewport": {'width': 1280, 'height': 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        if os.path.exists(AUTH_STATE_PATH):
            print("Loading existing session...")
            context = browser.new_context(storage_state=AUTH_STATE_PATH, **context_args)
        else:
            print("No session found. Fresh login required.")
            context = browser.new_context(**context_args)

        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        try:
            # 1. Login Logic
            page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")
            time.sleep(5)
            
            if "login" in page.url or page.locator('input[name="email"]').is_visible():
                print("Logging in...")
                page.goto("https://account.squarespace.com/login")
                page.get_by_label("Email address").fill(EMAIL)
                page.get_by_placeholder("Password", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Log In").click()
                
                # Wait for dashboard to confirm success
                page.wait_for_url("**/config**", timeout=60000)
                context.storage_state(path=AUTH_STATE_PATH)
                print("Login successful. Session saved.")

            # 2. Navigate to Blog Collection
            print(f"Navigating to Blog Editor: {TARGET_URL}")
            page.goto(TARGET_URL, wait_until="networkidle")
            time.sleep(8) # Extra buffer for Squarespace's heavy UI

            # 3. Handle the "Add Post" Button (Multi-Strategy)
            add_button = None
            selectors = [
                'button[aria-label="Add blog post"]',
                'button[data-test="blog-add-post"]',
                'button:has-text("Add")',
                '[aria-label="Add Post"]'
            ]

            # Strategy A: Check Main Page
            for sel in selectors:
                loc = page.locator(sel).first
                if loc.is_visible():
                    add_button = loc
                    break
            
            # Strategy B: Check Iframes (Site Preview Frame)
            if not add_button:
                for frame in page.frames:
                    for sel in selectors:
                        loc = frame.locator(sel).first
                        if loc.is_visible():
                            add_button = loc
                            break
                    if add_button: break

            if not add_button:
                page.screenshot(path="missing_button_debug.png")
                raise Exception("Could not locate the 'Add Post' button. See missing_button_debug.png")

            # 4. Processing Rows
            for i, row in enumerate(rows):
                if len(row) >= 4 and row[3].strip() == "Pending" and row[2].strip() == today_str:
                    title, content = row[0], row[1]
                    print(f"🎯 Processing: {title}")
                    update_sheet_status(service, i, "Processing")
                    
                    img_path = generate_image(title)
                    add_button.click()
                    
                    # Wait for editor to appear
                    page.wait_for_selector('h1[data-content-field="title"]', timeout=30000)
                    page.locator('h1[data-content-field="title"] .ProseMirror').fill(title)

                    # Content & Footer
                    editor = page.locator('.sqs-block-content .ProseMirror').last
                    footer = f"\n\n---\n**Need a delivery?** [Request a Quote]({BOOKING_LINK})"
                    editor.fill(content + footer)

                    # Featured Image Upload via Settings
                    if img_path:
                        print("Uploading image...")
                        page.get_by_role("button", name="Settings").click()
                        time.sleep(2)
                        page.locator('input[type="file"]').first.set_input_files(img_path)
                        time.sleep(8) # Wait for upload finish
                        page.get_by_role("button", name="Done").or_(page.get_by_role("button", name="Close")).click()

                    # Scheduling
                    print("Scheduling post...")
                    page.locator('button[data-test="publish-button-dropdown"]').click()
                    page.get_by_text("Schedule").click()
                    page.locator('div[data-test="date-time-picker"]').click()
                    page.keyboard.type(f"{row[2]} {SCHEDULE_TIME}")
                    page.keyboard.press("Enter")
                    time.sleep(2)
                    page.get_by_role("button", name="SCHEDULE").click()
                    
                    update_sheet_status(service, i, "Posted")
                    print(f"✅ Success: {title}")
                    
                    # Return to list for next post
                    page.goto(TARGET_URL, wait_until="networkidle")
                    time.sleep(5)

        except Exception as e:
            print(f"❌ Automation Error: {e}")
            page.screenshot(path="error_state.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()