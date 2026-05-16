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
    """Generates an image using Pollinations AI and saves it locally."""
    print(f"Generating image for: {prompt[:50]}...")
    seed = random.randint(1, 1000000)
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            print(f"Image saved as {filename}")
            return os.path.abspath(filename)
    except Exception as e:
        print(f"Failed to generate image: {e}")
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
        
        # Session Management: Load auth state if exists
        if os.path.exists(AUTH_STATE_PATH):
            print("Using existing session state...")
            context = browser.new_context(
                storage_state=AUTH_STATE_PATH,
                viewport={'width': 1280, 'height': 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        else:
            print("No session state found. Will attempt fresh login.")
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )

        page = context.new_page()
        Stealth().apply_stealth_sync(page) # Apply stealth to bypass detection

        try:
            # Check if we are already logged in
            page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")
            time.sleep(5)
            
            if "login" in page.url or page.locator('input[name="email"]').is_visible():
                print("Not logged in. Proceeding to login page...")
                page.goto("https://account.squarespace.com/login", wait_until="domcontentloaded")
                
                page.get_by_label("Email address").fill(EMAIL)
                page.get_by_placeholder("Password", exact=True).fill(PASSWORD)
                time.sleep(1) # Human-like pause
                
                page.get_by_role("button", name="Log In").click()
                print("Login clicked. Waiting for dashboard...")
                
                # Wait for navigation to dashboard (either /config or main account page)
                page.wait_for_selector('text=Dashboard', timeout=60000)
                print("Login successful.")
                
                # Save session state for next time
                context.storage_state(path=AUTH_STATE_PATH)
                print("Session state saved.")

            # STEP 4: Navigate to Blog
            print(f"Navigating to Blog: {TARGET_URL}")
            page.goto(TARGET_URL, wait_until="networkidle")

            for i, row in enumerate(rows):
                if len(row) >= 4 and row[3].strip() == "Pending" and row[2].strip() == today_str:
                    title = row[0]
                    content = row[1]
                    print(f"Processing Post: {title}")
                    update_sheet_status(service, i, "Processing")
                    
                    # Generate Image
                    img_path = generate_image(title)

                    # Click Add Post (+) in the sidebar
                    add_button = page.locator('button[aria-label="Add blog post"]').first
                    add_button.wait_for(state="visible", timeout=30000)
                    add_button.click()
                    
                    # Fill Title
                    page.wait_for_selector('h1[data-content-field="title"]', timeout=20000)
                    page.locator('h1[data-content-field="title"] .ProseMirror').fill(title)
                    
                    # Fill Content
                    editor = page.locator('.sqs-block-content .ProseMirror').last
                    footer = f"\n\n---\n**Need a delivery?** [Quote]({BOOKING_LINK})"
                    editor.fill(content + footer)

                    # Upload Featured Image (if generated)
                    if img_path:
                        print("Uploading featured image...")
                        page.get_by_role("button", name="Settings").click()
                        time.sleep(2)
                        
                        file_input = page.locator('input[type="file"]').first
                        file_input.set_input_files(img_path)
                        
                        # Wait for upload to complete
                        time.sleep(5)
                        page.get_by_role("button", name="Close").or_(page.get_by_role("button", name="Done")).click()

                    # Schedule and Publish
                    print("Scheduling post...")
                    page.locator('button[data-test="publish-button-dropdown"]').click()
                    page.get_by_text("Schedule").click()
                    
                    # Set Time
                    page.locator('div[data-test="date-time-picker"]').click()
                    page.keyboard.type(f"{row[2]} {SCHEDULE_TIME}")
                    page.keyboard.press("Enter")
                    time.sleep(2)
                    
                    page.get_by_role("button", name="SCHEDULE").click()
                    
                    update_sheet_status(service, i, "Posted")
                    print(f"Post Successful: {title}")
                    
                    # Back to blog list to process next
                    page.goto(TARGET_URL, wait_until="networkidle")

        except Exception as e:
            print(f"Error occurred: {e}")
            page.screenshot(path="error_state.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()