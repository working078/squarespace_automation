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
BASE_URL = "https://coconut-radish-an89.squarespace.com/config/pages/6a00f5fd27ce801ca25aa32e"
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
    full_prompt = f"Professional transport logistics photography, Australian trucking, {prompt}"
    encoded_prompt = urllib.parse.quote(full_prompt)
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
        context_args = {
            "viewport": {'width': 1280, 'height': 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        
        if os.path.exists(AUTH_STATE_PATH):
            print("Session found. Loading storage state...")
            context = browser.new_context(storage_state=AUTH_STATE_PATH, **context_args)
        else:
            print("No session found. Preparing fresh login...")
            context = browser.new_context(**context_args)

        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        try:
            # 1. AUTH CHECK
            page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")
            time.sleep(5)
            page.screenshot(path="01_initial_landing.png") # CHECKPOINT 1

            if "login" in page.url or page.locator('input[name="email"]').is_visible():
                print("Performing fresh login...")
                page.goto("https://account.squarespace.com/login")
                page.get_by_label("Email address").fill(EMAIL)
                page.get_by_placeholder("Password", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Log In").click()
                page.wait_for_url("**/config**", timeout=60000)
                context.storage_state(path=AUTH_STATE_PATH)
                print("Login successful.")

            # 2. CHECK DATE & PROCESS
            print(f"Checking Sheet rows for today's date: {today_str}")
            found_work = False
            
            for i, row in enumerate(rows):
                if len(row) >= 4 and row[3].strip() == "Pending" and row[2].strip() == today_str:
                    found_work = True
                    title, content = row[0], row[1]
                    print(f"🚀 Processing: {title}")
                    update_sheet_status(service, i, "Processing")
                    
                    img_path = generate_image(title)
                    composer_url = f"{BASE_URL}/edit"
                    page.goto(composer_url, wait_until="networkidle")
                    
                    page.wait_for_selector('h1[data-content-field="title"]', timeout=45000)
                    page.screenshot(path="02_editor_loaded.png") # CHECKPOINT 2

                    page.locator('h1[data-content-field="title"] .ProseMirror').fill(title)
                    editor = page.locator('.sqs-block-content .ProseMirror').last
                    footer = f"\n\n---\n**Need a delivery?** [Request a Quote]({BOOKING_LINK})"
                    editor.fill(content + footer)

                    if img_path:
                        page.get_by_role("button", name="Settings").click()
                        time.sleep(3)
                        page.locator('input[type="file"]').first.set_input_files(img_path)
                        time.sleep(10)
                        page.get_by_role("button", name="Done").or_(page.get_by_role("button", name="Close")).click()

                    page.locator('button[data-test="publish-button-dropdown"]').click()
                    page.get_by_text("Schedule").click()
                    page.locator('div[data-test="date-time-picker"]').click()
                    page.keyboard.type(f"{row[2]} {SCHEDULE_TIME}")
                    page.keyboard.press("Enter")
                    time.sleep(2)
                    page.get_by_role("button", name="SCHEDULE").click()
                    
                    update_sheet_status(service, i, "Posted")
                    print(f"✅ Success: {title}")
                    if img_path and os.path.exists(img_path): os.remove(img_path)

            if not found_work:
                print("No pending posts found for today. Taking final view screenshot.")
                page.goto(BASE_URL)
                time.sleep(5)
                page.screenshot(path="03_nothing_to_do_today.png") # CHECKPOINT 3

        except Exception as e:
            print(f"❌ Error: {e}")
            page.screenshot(path="final_error_state.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()