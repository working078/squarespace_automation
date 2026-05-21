import os
import json
import time
import re
import requests
import random
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
# Blog is AU-based; GitHub Actions runs in UTC — compare dates in Melbourne time.
LOCAL_TZ = ZoneInfo("Australia/Melbourne")
SHEETS_DATE_EPOCH = datetime(1899, 12, 30)

DATE_FORMATS = (
    "%d/%m/%y",
    "%d/%m/%Y",
    "%d-%m-%y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%y",
    "%m/%d/%Y",
)
COLUMN_LETTERS = ("A", "B", "C", "D")


def sheet_range(tab, cell_range):
    escaped = tab.replace("'", "''")
    return f"'{escaped}'!{cell_range}"


def get_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if creds_json:
        return service_account.Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    return service_account.Credentials.from_service_account_file('credentials.json', scopes=SCOPES)

def pad_row(row, width=4):
    row = list(row)
    while len(row) < width:
        row.append("")
    return row


def normalize_status(value):
    return str(value).strip().casefold()


def is_pending_status(value):
    s = normalize_status(value)
    return s == "pending" or s.startswith("pending")


def serial_to_date(serial):
    days = float(serial)
    whole_days = int(days)
    return (SHEETS_DATE_EPOCH + timedelta(days=whole_days)).date()


def parse_sheet_date(value):
    """Parse a sheet date cell (formatted string, serial number, or ISO)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return serial_to_date(value)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return serial_to_date(float(text))
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def today_local():
    return datetime.now(LOCAL_TZ).date()


def list_sheet_tabs(service):
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def column_has_pending(service, tab):
    for cell in _fetch_column(service, tab, "D", "FORMATTED_VALUE"):
        if is_pending_status(cell):
            return True
    return False


def resolve_sheet_tab(service):
    """Use SHEET_TAB env var, else the tab that contains Pending rows."""
    override = os.getenv("SHEET_TAB", "").strip()
    if override:
        print(f"Using spreadsheet tab from SHEET_TAB: {override!r}")
        return override

    tabs = list_sheet_tabs(service)
    if not tabs:
        return "Sheet1"

    for tab in tabs:
        if column_has_pending(service, tab):
            print(f"Using spreadsheet tab with Pending rows: {tab!r}")
            return tab

    tab = tabs[0]
    print(f"No Pending rows found in any tab; defaulting to first tab: {tab!r}")
    return tab


def _fetch_column(service, tab, letter, value_render_option):
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_range(tab, f"{letter}2:{letter}"),
        valueRenderOption=value_render_option,
    ).execute()
    values = result.get("values", [])
    return [row[0] if row else "" for row in values]


def fetch_sheet_rows(service, tab):
    """
    Fetch A–D row-by-row with per-column requests so empty cells do not
    shift Title/Content/Date/Status into the wrong indices.
    """
    raw_cols = {
        letter: _fetch_column(service, tab, letter, "UNFORMATTED_VALUE")
        for letter in COLUMN_LETTERS
    }
    formatted_dates = _fetch_column(service, tab, "C", "FORMATTED_VALUE")

    row_count = max((len(col) for col in raw_cols.values()), default=0)
    rows = []
    for offset in range(row_count):
        row = [
            raw_cols[letter][offset] if offset < len(raw_cols[letter]) else ""
            for letter in COLUMN_LETTERS
        ]
        if not any(str(cell).strip() for cell in row):
            continue
        formatted_date = formatted_dates[offset] if offset < len(formatted_dates) else ""
        rows.append((offset, row, formatted_date))
    print(f"Loaded {len(rows)} non-empty data row(s) from tab {tab!r}")
    return rows


def dump_row_diagnostics(rows_with_meta):
    print("--- Sheet diagnostic (no posts queued) ---")
    for offset, row, formatted_date in rows_with_meta:
        sheet_row = offset + 2
        title_preview = repr(str(row[0])[:80])
        print(
            f"Row {sheet_row}: A={title_preview} | B(len)={len(str(row[1]))} | "
            f"C(raw)={row[2]!r} C(fmt)={formatted_date!r} | D={row[3]!r}"
        )
    print("--- End diagnostic ---")


def select_pending_rows(rows_with_meta):
    """Decide which rows to process; log skip reasons for debugging."""
    today = today_local()
    print(f"Today (Australia/Melbourne): {today.isoformat()}")
    pending = []
    for offset, row, formatted_date in rows_with_meta:
        sheet_row = offset + 2
        status = row[3]
        if not is_pending_status(status):
            continue
        post_date = parse_sheet_date(row[2])
        if post_date is None and formatted_date:
            post_date = parse_sheet_date(formatted_date)
        if post_date is None:
            print(
                f"Row {sheet_row}: skip — could not parse date "
                f"raw={row[2]!r} formatted={formatted_date!r} status={status!r}"
            )
            continue
        if post_date > today:
            print(
                f"Row {sheet_row}: skip — scheduled {post_date.isoformat()} "
                f"is after today {today.isoformat()}"
            )
            continue
        title_preview = str(row[0])[:60]
        print(
            f"Row {sheet_row}: queued — date {post_date.isoformat()}, "
            f"title={title_preview!r}..."
        )
        pending.append((offset, row, post_date))
    return pending


def update_sheet_status(service, tab, row_index, status):
    range_name = sheet_range(tab, f"D{row_index + 2}")
    body = {'values': [[status]]}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=range_name,
        valueInputOption="USER_ENTERED", body=body).execute()

def build_excerpt(content, max_len=220):
    """Short teaser for the blog index (Metro Express–style card excerpt)."""
    text = re.sub(r"\s+", " ", str(content).strip())
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "..."


def format_blog_body(content, post_date):
    """Structure body copy: paragraphs + footer CTA."""
    raw = str(content).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw] if raw else [""]
    body = "\n\n".join(paragraphs)
    footer = (
        f"\n\n---\n\n"
        f"**Need a delivery?** [Request a Quote]({BOOKING_LINK})"
    )
    return body + footer


def dismiss_site_settings_modal(page):
    """Close the site-wide Settings panel if it was opened by mistake."""
    if page.get_by_role("heading", name="Settings").count() > 0 and page.get_by_text("Website", exact=True).count() > 0:
        print("Closing site Settings modal (not post settings)...")
        close_btn = page.locator('button[aria-label="Close"]')
        if close_btn.count() > 0:
            close_btn.first.click()
        else:
            page.keyboard.press("Escape")
        time.sleep(1)


def is_valid_jpeg(data):
    return isinstance(data, bytes) and len(data) > 10000 and data[:3] == b"\xff\xd8\xff"


def set_files_on_any_input(page, frame, img_path):
    """Attach a file to the first available upload input (iframe or parent page)."""
    for root_name, root in (("editor", frame), ("page", page)):
        inputs = root.locator('input[type="file"]')
        count = inputs.count()
        for i in range(count):
            try:
                inputs.nth(i).set_input_files(img_path, timeout=8000)
                print(f"Attached image via {root_name} file input #{i}")
                return True
            except Exception:
                continue
    return False


def insert_image_in_editor(page, frame, img_path):
    """
    Add an image block at the top of the post body (inside the editor iframe).
    Squarespace blog templates use this as the card/hero image on /blog/.
    """
    print(f"Inserting image in post editor: {img_path}")
    editor = frame.locator(".tiptap.ProseMirror").first
    editor.click()
    time.sleep(1)
    editor.press("Home")
    time.sleep(0.5)
    editor.press("/")
    time.sleep(1.5)

    for selector in (
        frame.get_by_role("option", name="Image"),
        frame.get_by_text("Image", exact=True),
        frame.locator('[data-item-name="image"]'),
    ):
        if selector.count() > 0:
            selector.first.click()
            break
    else:
        raise RuntimeError("Image block not found in editor slash menu")

    time.sleep(2)
    if set_files_on_any_input(page, frame, img_path):
        time.sleep(12)
        return

    upload_triggers = [
        frame.get_by_text(re.compile(r"upload|computer|browse|device", re.I)),
        page.get_by_text(re.compile(r"upload|computer|browse|device", re.I)),
    ]
    for trigger in upload_triggers:
        if trigger.count() == 0:
            continue
        with page.expect_file_chooser(timeout=15000) as fc_info:
            trigger.first.click(force=True)
        fc_info.value.set_files(img_path)
        time.sleep(12)
        print("Image attached via file chooser.")
        return

    raise RuntimeError("No file upload control found after adding Image block")


def try_publish_menu_post_settings(page, img_path=None, excerpt=None):
    """
    Optional fallback: PUBLISH dropdown → post settings (thumbnail field).
    Never uses data-testid=settings-icon (that opens site Settings).
    """
    dismiss_site_settings_modal(page)
    dropdown = page.locator('button[data-test="publish-button-dropdown"]')
    if dropdown.count() == 0:
        return False

    dropdown.first.click()
    time.sleep(1)
    opened = False
    for label in ("Post Settings", "Settings", "SEO", "Options"):
        item = page.get_by_text(label, exact=False)
        if item.count() > 0:
            item.first.click()
            opened = True
            break
    if not opened:
        page.keyboard.press("Escape")
        return False

    time.sleep(2)
    if img_path and os.path.exists(img_path):
        if not set_files_on_any_input(page, page, img_path):
            for click_target in (
                page.get_by_text(re.compile(r"add|upload|replace", re.I)),
                page.locator('[aria-label*="image" i]'),
            ):
                if click_target.count() > 0:
                    click_target.first.click()
                    time.sleep(1)
                    if set_files_on_any_input(page, page, img_path):
                        break
        time.sleep(10)

    if excerpt:
        field = page.get_by_label("Excerpt", exact=False)
        if field.count() == 0:
            field = page.locator('textarea[placeholder*="Excerpt" i]')
        if field.count() > 0:
            field.first.fill(excerpt)

    for label in ("Done", "Close", "Save"):
        btn = page.get_by_role("button", name=label)
        if btn.count() > 0:
            btn.first.click()
            time.sleep(1)
            return True
    page.keyboard.press("Escape")
    return True


def configure_post_metadata(page, frame, img_path=None, excerpt=None):
    """Upload hero/card image and set excerpt when controls are available."""
    dismiss_site_settings_modal(page)
    image_ok = not img_path

    if img_path and os.path.exists(img_path):
        errors = []
        try:
            insert_image_in_editor(page, frame, img_path)
            image_ok = True
            print("Image upload succeeded via editor image block.")
        except Exception as exc:
            errors.append(f"editor: {exc}")
            dismiss_site_settings_modal(page)
            if try_publish_menu_post_settings(page, img_path=img_path, excerpt=excerpt):
                image_ok = True
                print("Image upload succeeded via publish menu settings.")
            else:
                errors.append("publish menu: panel not available")

        if not image_ok:
            page.screenshot(path="image_upload_failed.png")
            raise RuntimeError("Image upload failed — " + "; ".join(errors))

    if excerpt and image_ok:
        try:
            try_publish_menu_post_settings(page, excerpt=excerpt)
        except Exception:
            print("Excerpt not set; blog listing will use the opening paragraph.")


def generate_image(prompt, filename="blog_image.jpg"):
    print(f"Generating image for: {prompt[:50]}...")
    seed = random.randint(1, 1000000)
    full_prompt = f"Professional transport logistics photography, Australian trucking, {prompt}"
    encoded_prompt = urllib.parse.quote(full_prompt)
    params = f"width=1024&height=1024&seed={seed}&model=flux"
    urls = [
        f"https://gen.pollinations.ai/image/{encoded_prompt}?{params}",
        f"https://image.pollinations.ai/prompt/{encoded_prompt}?{params}",
    ]
    headers = {}
    api_key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for url in urls:
        try:
            response = requests.get(url, timeout=90, headers=headers, allow_redirects=True)
            if response.status_code == 200 and is_valid_jpeg(response.content):
                with open(filename, "wb") as f:
                    f.write(response.content)
                print(f"Image saved ({len(response.content) // 1024} KB) from {url.split('/')[2]}")
                return os.path.abspath(filename)
            preview = response.content[:40]
            print(f"Bad image from {url.split('/')[2]}: status={response.status_code}, bytes={len(response.content)}, head={preview!r}")
        except Exception as e:
            print(f"Request failed for {url.split('/')[2]}: {e}")
    return None

def run_automation():
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    tab = resolve_sheet_tab(service)
    rows_with_meta = fetch_sheet_rows(service, tab)
    work_items = select_pending_rows(rows_with_meta)
    if not work_items:
        dump_row_diagnostics(rows_with_meta)
        print("No pending posts found for today or earlier.")
        return

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

            # 2. PROCESS QUEUED ROWS
            for offset, row, post_date in work_items:
                try:
                    title, content = row[0], row[1]
                    sheet_row = offset + 2
                    print(f"Processing row {sheet_row}: {title} ({post_date.isoformat()})")
                    update_sheet_status(service, tab, offset, "Processing")

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

                    # Image first (hero/card), then body text below it
                    if not img_path:
                        print("WARNING: No valid JPEG generated — post will publish without image.")
                    configure_post_metadata(
                        page,
                        frame,
                        img_path=img_path if img_path and os.path.exists(img_path) else None,
                        excerpt=build_excerpt(content),
                    )

                    body_text = format_blog_body(content, post_date)
                    frame.locator('.tiptap.ProseMirror').last.fill(body_text)
                    page.screenshot(path=f"before_publish_{offset}.png")

                    # Publish immediately
                    print("Publishing post...")
                    page.get_by_text("PUBLISH").first.click()
                    time.sleep(2)
                    page.get_by_text("PUBLISH").last.click()
                    time.sleep(5)

                    if page.get_by_text("Done").count() > 0:
                        page.get_by_text("Done").last.click()

                    page.screenshot(path=f"after_publish_{offset}.png")

                    update_sheet_status(service, tab, offset, "Posted")
                    print(f"Success: {title}")
                    if img_path and os.path.exists(img_path):
                        os.remove(img_path)

                    # Short break before next post
                    time.sleep(5)
                except Exception as row_error:
                    print(f"Error on row {offset + 2}: {row_error}")
                    continue

        except Exception as e:
            print(f"Fatal Error: {e}")
            page.screenshot(path="fatal_error.png")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()