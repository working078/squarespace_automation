import os
import json
import time
import requests
import urllib.parse
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

# --- 1. CONFIGURATION ---
SPREADSHEET_ID = '18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
TARGET_URL = "https://www.squarespace.com/config/pages/6a00f5fd27ce801ca25aa32e"

# Updated Client Links
BOOKING_LINK = "https://forms.clickup.com/90161562352/f/2kz0rgqg-676/WM5FMNFXZQWBKHRIBF"
TRIBE_RURAL_HOME = "https://www.triberural.com.au/"
SCHEDULE_TIME = "07:00 AM" # Updated to 7 AM

def get_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return service_account.Credentials.from_service_account_file('credentials.json', scopes=SCOPES)

def get_sheet_data(creds):
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range="Sheet1!A2:D").execute()
    return result.get('values', []), service

def update_sheet_status(service, row_index, status):
    range_name = f"Sheet1!D{row_index+2}"
    body = {'values': [[status]]}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=range_name,
        valueInputOption="USER_ENTERED", body=body).execute()

def generate_image(title):
    prompt = f"Professional logistics and transport photography, {title}, Australian regional freight style, high resolution"
    encoded_prompt = urllib.parse.quote(prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=768&nologo=true"
    path = "temp_blog_img.jpg"
    try:
        response = requests.get(image_url, timeout=30)
        if response.status_code == 200:
            with open(path, 'wb') as f: f.write(response.content)
            return path
    except: return None
    return None

def run_automation():
    creds = get_credentials()
    rows, service = get_sheet_data(creds)
    
    # Get Today's Date in DD/MM/YY format
    today_str = datetime.now().strftime("%d/%m/%y")
    print(f"Checking for articles scheduled for: {today_str}")

    EMAIL = os.getenv("SQ_EMAIL")
    PASSWORD = os.getenv("SQ_PASSWORD")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=os.getenv("GITHUB_ACTIONS") == "true")
        page = browser.new_page()

        # Login
        page.goto("https://account.squarespace.com/login")
        page.get_by_label("Email address").fill(EMAIL)
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Log In").click()
        page.wait_for_load_state("networkidle")
        
        page.goto(TARGET_URL)
        page.wait_for_selector('button:has-text("Add")', timeout=60000)

        for i, row in enumerate(rows):
            if len(row) >= 4:
                title, content, date_val, status = row[0], row[1], row[2], row[3]
                
                # Check if it is Pending and matches Today
                if status.strip() == "Pending" and date_val.strip() == today_str:
                    print(f"🎯 Scheduling post for today: {title}")
                    update_sheet_status(service, i, "Processing")

                    try:
                        image_path = generate_image(title)
                        page.get_by_role("button", name="Add").click()
                        page.wait_for_selector('h1[data-content-field="title"]', timeout=20000)
                        page.locator('h1[data-content-field="title"] .ProseMirror').fill(title)

                        if image_path:
                            page.locator('.sqs-block-content').first.hover()
                            page.locator('[data-test="insert-point"]').first.click()
                            page.get_by_text("Image", exact=True).click()
                            page.set_input_files('input[type="file"]', image_path)
                            time.sleep(7)

                        # Updated Footer with Tribe Rural Link
                        footer = (
                            f"\n\n---\n"
                            f"**Need a reliable delivery service?**\n"
                            f"👉 [Request a quote now]({BOOKING_LINK})\n"
                            f"🏠 [Visit Tribe Rural]({TRIBE_RURAL_HOME})"
                        )
                        
                        editor = page.locator('.sqs-block-content .ProseMirror').last
                        editor.click()
                        editor.fill(content + footer)

                        # Set Schedule to 7:00 AM
                        page.locator('button[data-test="publish-button-dropdown"]').click()
                        page.get_by_text("Schedule").click()
                        page.locator('div[data-test="date-time-picker"]').click()
                        page.keyboard.press("Control+A")
                        page.keyboard.type(f"{date_val} {SCHEDULE_TIME}")
                        page.keyboard.press("Enter")
                        
                        page.get_by_role("button", name="SCHEDULE").click()
                        
                        update_sheet_status(service, i, "Posted")
                        if os.path.exists(image_path): os.remove(image_path)
                    except Exception as e:
                        print(f"Error: {e}")
                        update_sheet_status(service, i, "Failed")
                else:
                    print(f"Skipping {title}: Date/Status mismatch.")

        browser.close()

if __name__ == "__main__":
    run_automation()