import os
import json
import time
import requests
import urllib.parse
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
SPREADSHEET_ID = '18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
TARGET_URL = "https://www.squarespace.com/config/pages/6a00f5fd27ce801ca25aa32e"
BOOKING_LINK = "https://forms.clickup.com/90161562352/f/2kz0rgqg-676/WM5FMNFXZQWBKHRIBF"
TRIBE_RURAL_HOME = "https://www.triberural.com.au/"
SCHEDULE_TIME = "07:00 AM"

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
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = context.new_page()

        try:
            # STEP 1: Landing on Login
            print("Navigating to Login...")
            page.goto("https://account.squarespace.com/login", wait_until="domcontentloaded")
            page.screenshot(path="step1_login_landing.png")

            # STEP 2: Filling Credentials
            page.get_by_label("Email address").fill(EMAIL)
            page.get_by_placeholder("Password", exact=True).fill(PASSWORD)
            page.screenshot(path="step2_creds_filled.png")
            
            page.get_by_role("button", name="Log In").click()
            print("Login clicked. Waiting for dashboard...")
            
            # STEP 3: The "Wait and See"
            time.sleep(15) 
            page.screenshot(path="step3_after_login_attempt.png")

            # Check if we made it to the config area
            page.wait_for_url("**/config**", timeout=45000)
            print("Successfully reached Dashboard.")

            # STEP 4: Navigate to Blog
            page.goto(TARGET_URL, wait_until="networkidle")
            page.screenshot(path="step4_blog_page.png")

            add_button = page.locator('button:has-text("Add"), [aria-label="Add Post"]').first
            add_button.wait_for(state="visible", timeout=30000)

            for i, row in enumerate(rows):
                if len(row) >= 4 and row[3].strip() == "Pending" and row[2].strip() == today_str:
                    print(f"Processing: {row[0]}")
                    update_sheet_status(service, i, "Processing")
                    
                    add_button.click()
                    page.wait_for_selector('h1[data-content-field="title"]', timeout=20000)
                    page.locator('h1[data-content-field="title"] .ProseMirror').fill(row[0])
                    
                    # (Rest of post logic remains same...)
                    editor = page.locator('.sqs-block-content .ProseMirror').last
                    footer = f"\n\n---\n**Need a delivery?** [Quote]({BOOKING_LINK})"
                    editor.fill(row[1] + footer)

                    page.locator('button[data-test="publish-button-dropdown"]').click()
                    page.get_by_text("Schedule").click()
                    page.locator('div[data-test="date-time-picker"]').click()
                    page.keyboard.type(f"{row[2]} {SCHEDULE_TIME}")
                    page.keyboard.press("Enter")
                    time.sleep(2)
                    page.get_by_role("button", name="SCHEDULE").click()
                    
                    update_sheet_status(service, i, "Posted")
                    print(f"Post Successful: {row[0]}")

        except Exception as e:
            print(f"Error occurred: {e}")
            page.screenshot(path="final_error_state.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()