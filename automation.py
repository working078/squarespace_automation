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
            # 1. AUTHENTICATION & ERROR CHECK
            print("Navigating to Squarespace...")
            page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")
            time.sleep(10)
            
            # Detect login page or error popup
            if "login" in page.url or page.locator('input[name="email"]').is_visible() or page.locator('text=Couldn\'t load items').is_visible():
                print("Session expired or invalid. Performing fresh login...")
                page.goto("https://account.squarespace.com/login")
                page.get_by_label("Email address").fill(EMAIL)
                page.get_by_placeholder("Password", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Log In").click()
                page.wait_for_url("**/config**", timeout=60000)
                context.storage_state(path=AUTH_STATE_PATH)
                print("Login successful.")

            # 2. ITERATE ROWS
            found_any_work = False
            for i, row in enumerate(rows):
                if len(row) >= 4 and row[3].strip() == "Pending":
                    try:
                        row_date_str = row[2].strip()
                        row_date = datetime.strptime(row_date_str, "%d/%m/%y")
                        
                        # Process if date is today or in the past
                        if row_date.date() <= datetime.now().date():
                            found_any_work = True
                            title, content = row[0], row[1]
                            print(f"Processing: {title} ({row_date_str})")
                            update_sheet_status(service, i, "Processing")
                            
                            img_path = generate_image(title)
                            
                            print(f"Navigating to blog list...")
                            page.goto(BASE_URL, wait_until="load", timeout=60000)
                            
                            # Click Add Post (+) in sidebar
                            print("Opening new post editor...")
                            add_button = page.locator('button[aria-label="Add blog post"]').first
                            add_button.wait_for(state="visible", timeout=45000)
                            add_button.click()
                            
                            # Wait for the editor to initialize
                            time.sleep(15)
                            
                            # --- IFRAME HANDLING ---
                            print("Accessing editor iframe...")
                            iframe_handle = page.wait_for_selector('iframe#sqs-site-frame', timeout=60000)
                            frame = iframe_handle.content_frame()
                            
                            # Wait for Title inside frame
                            frame.wait_for_selector('h1.entry-title .ProseMirror', timeout=30000)
                            frame.locator('h1.entry-title .ProseMirror').fill(title)
                            
                            # Fill Content
                            frame.locator('.tiptap.ProseMirror').fill(content + f"\n\n---\n**Need a delivery?** [Request a Quote]({BOOKING_LINK})")

                            # Upload Image
                            if img_path:
                                print("Uploading featured image...")
                                page.evaluate('document.querySelector("[data-testid=\\"settings-icon\\"]").click()')
                                time.sleep(5)
                                page.locator('input[type="file"]').first.set_input_files(img_path)
                                time.sleep(15) 
                                page.get_by_role("button", name="Done").or_(page.get_by_role("button", name="Close")).click()

                            # Scheduling
                            print("Scheduling post...")
                            page.locator('button[data-test="publish-button-dropdown"]').click()
                            page.get_by_text("Schedule").click()
                            page.locator('div[data-test="date-time-picker"]').click()
                            page.keyboard.type(f"{row_date_str} {SCHEDULE_TIME}")
                            page.keyboard.press("Enter")
                            time.sleep(5)
                            page.get_by_role("button", name="SCHEDULE").click()
                            
                            update_sheet_status(service, i, "Posted")
                            print(f"Success: {title}")
                            if img_path and os.path.exists(img_path): os.remove(img_path)
                            
                            # Short break before next post
                            time.sleep(5)
                    except Exception as row_error:
                        print(f"Error on row {i+2}: {row_error}")
                        continue

            if not found_any_work:
                print("No pending posts found for today or earlier.")

        except Exception as e:
            print(f"Fatal Error: {e}")
            page.screenshot(path="fatal_error.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()