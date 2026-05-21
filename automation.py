import os
import json
import time
import re
import base64
import requests
import random
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright
# Support both playwright-stealth 1.x (stealth_sync) and older versions (Stealth class)
try:
    from playwright_stealth import stealth_sync
    def apply_stealth(page):
        stealth_sync(page)
except ImportError:
    from playwright_stealth import Stealth
    def apply_stealth(page):
        Stealth().apply_stealth_sync(page)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
SPREADSHEET_ID  = '18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts'
SCOPES          = ['https://www.googleapis.com/auth/spreadsheets']

# FIX #1: Use the direct /edit composer URL — no page-ID fragility, no "Add Post" button hunt
BASE_URL        = "https://coconut-radish-an89.squarespace.com"
COMPOSER_URL    = f"{BASE_URL}/edit"          # opens a blank post canvas immediately

BOOKING_LINK    = "https://forms.clickup.com/90161562352/f/2kz0rgqg-676/WM5FMNFXZQWBKHRIBF"
AUTH_STATE_PATH = 'auth.json'
LOCAL_TZ        = ZoneInfo("Australia/Melbourne")
SHEETS_DATE_EPOCH = datetime(1899, 12, 30)

DATE_FORMATS = (
    "%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y",
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%y", "%m/%d/%Y",
)
COLUMN_LETTERS = ("A", "B", "C", "D")


# ---------------------------------------------------------------------------
# GOOGLE SHEETS HELPERS
# ---------------------------------------------------------------------------

def sheet_range(tab, cell_range):
    escaped = tab.replace("'", "''")
    return f"'{escaped}'!{cell_range}"


def get_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if creds_json:
        return service_account.Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
    return service_account.Credentials.from_service_account_file(
        'credentials.json', scopes=SCOPES
    )


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
    days  = float(serial)
    whole = int(days)
    return (SHEETS_DATE_EPOCH + timedelta(days=whole)).date()


def parse_sheet_date(value):
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


def _fetch_column(service, tab, letter, value_render_option):
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_range(tab, f"{letter}2:{letter}"),
        valueRenderOption=value_render_option,
    ).execute()
    values = result.get("values", [])
    return [row[0] if row else "" for row in values]


def column_has_pending(service, tab):
    for cell in _fetch_column(service, tab, "D", "FORMATTED_VALUE"):
        if is_pending_status(cell):
            return True
    return False


def resolve_sheet_tab(service):
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


def fetch_sheet_rows(service, tab):
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
        valueInputOption="USER_ENTERED", body=body
    ).execute()


# ---------------------------------------------------------------------------
# CONTENT FORMATTING
# ---------------------------------------------------------------------------

def format_blog_body(content, post_date):
    raw        = str(content).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw] if raw else [""]
    body      = "\n\n".join(paragraphs)
    published = post_date.strftime("%d %B %Y")
    footer = (
        "\n\n"
        "—\n\n"
        "**Ready to book freight or a small move?**\n\n"
        "Tribe Rural Logistics supports renters and businesses across Mansfield and "
        "regional Victoria with direct transport, clear communication, and dependable "
        "local service.\n\n"
        f"**[Request a quote online →]({BOOKING_LINK})**\n\n"
        f"*Published {published} · Tribe Rural Logistics Pty Ltd · ABN 40 677 940 840*"
    )
    return body + footer


# ---------------------------------------------------------------------------
# IMAGE GENERATION
# ---------------------------------------------------------------------------

def is_valid_jpeg(data):
    return isinstance(data, bytes) and len(data) > 10000 and data[:3] == b"\xff\xd8\xff"


def generate_image(prompt, filename="blog_image.jpg"):
    print(f"Generating image for: {prompt[:50]}...")
    seed         = random.randint(1, 1000000)
    full_prompt  = f"Professional transport logistics photography, Australian trucking, {prompt}"
    encoded      = urllib.parse.quote(full_prompt)
    params       = f"width=1024&height=1024&seed={seed}&model=flux"
    api_key      = os.getenv("POLLINATIONS_API_KEY", "").strip()
    urls         = [f"https://image.pollinations.ai/prompt/{encoded}?{params}"]
    if api_key:
        urls.insert(0, f"https://gen.pollinations.ai/image/{encoded}?{params}")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    for url in urls:
        try:
            response = requests.get(url, timeout=90, headers=headers, allow_redirects=True)
            if response.status_code == 200 and is_valid_jpeg(response.content):
                with open(filename, "wb") as f:
                    f.write(response.content)
                print(f"Image saved ({len(response.content) // 1024} KB)")
                return os.path.abspath(filename), url
            print(
                f"Bad image from {url.split('/')[2]}: "
                f"status={response.status_code}, bytes={len(response.content)}"
            )
        except Exception as e:
            print(f"Request failed for {url.split('/')[2]}: {e}")
    return None, None


# ---------------------------------------------------------------------------
# BROWSER / SQUARESPACE HELPERS
# ---------------------------------------------------------------------------

def get_editor_content_frame(page):
    iframe = page.query_selector("iframe#sqs-site-frame")
    if not iframe:
        return None
    return iframe.content_frame()


def wait_for_editor_iframe(page, timeout=60000):
    """Wait for the editor iframe to appear and return a frame_locator."""
    page.wait_for_selector("iframe#sqs-site-frame", state="visible", timeout=timeout)
    return page.frame_locator("iframe#sqs-site-frame")


def dismiss_modal_if_open(page):
    """Dismiss any accidental site-wide Settings/modal that blocks the UI."""
    try:
        if (
            page.get_by_role("heading", name="Settings").count() > 0
            and page.get_by_text("Website", exact=True).count() > 0
        ):
            print("Dismissing unexpected site Settings modal...")
            close_btn = page.locator('button[aria-label="Close"]')
            if close_btn.count() > 0:
                close_btn.first.click()
            else:
                page.keyboard.press("Escape")
            time.sleep(1)
    except Exception:
        pass


def fill_title(frame, title):
    """Type the post title into the ProseMirror h1 field inside the iframe."""
    title_loc = frame.locator("h1.entry-title .ProseMirror, h1[data-content-field='title'] .ProseMirror")
    title_loc.first.wait_for(state="visible", timeout=30000)
    title_loc.first.click()
    title_loc.first.fill(title)
    print(f"Title filled: {title[:60]}")
    time.sleep(1)


def fill_body(frame, body_text):
    """Type post body into the last ProseMirror editor (not the title one)."""
    body_loc = frame.locator(".tiptap.ProseMirror").last
    body_loc.wait_for(state="visible", timeout=20000)
    body_loc.click()
    body_loc.fill(body_text)
    print("Body filled.")
    time.sleep(1)


# FIX #4: Target POST settings specifically, never the site-wide Settings panel
def upload_featured_image_via_post_settings(page, img_path):
    """
    Open the POST settings panel (not the site Settings) and upload the
    featured/thumbnail image.  Uses precise selectors to avoid the wrong button.
    """
    if not img_path or not os.path.exists(img_path):
        print("No image file — skipping featured image upload.")
        return False

    print("Opening Post Settings panel for featured image upload...")
    dismiss_modal_if_open(page)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    # FIX #4: Only click the POST-level settings, never the global "Settings" nav item
    post_settings_btn = (
        page.locator('[data-test="post-settings-button"]')
        .or_(page.locator('button[aria-label="Post Settings"]'))
        .or_(page.locator('button[aria-label="Options"]'))           # some SQS versions
        .or_(page.locator('[data-testid="post-settings-button"]'))
    )

    if post_settings_btn.count() == 0:
        print("Post Settings button not found — skipping featured image.")
        return False

    post_settings_btn.first.click(force=True)
    time.sleep(3)

    # Wait for the file input to appear in the settings panel
    file_input = page.locator('input[type="file"]').first
    try:
        file_input.wait_for(state="attached", timeout=15000)
        file_input.set_input_files(img_path)
        # FIX #5: Wait for upload to finish instead of blind sleep
        page.wait_for_function(
            "() => !document.querySelector('.sqs-upload-progress, [data-uploading=\"true\"]')",
            timeout=30000,
        )
        print("Featured image uploaded via Post Settings.")
    except Exception as e:
        print(f"Featured image upload failed: {e}")
        page.screenshot(path="featured_image_error.png")
        page.keyboard.press("Escape")
        return False

    # Close the panel
    for label in ("Done", "Close", "Save"):
        btn = page.get_by_role("button", name=label)
        if btn.count() > 0:
            try:
                btn.first.click(force=True)
                break
            except Exception:
                continue
    time.sleep(2)
    return True


def run_publish_confirmation_flow(page):
    """
    Direct publish flow — no Save Draft step.
    Clicks the main Publish button/dropdown, then confirms if a modal appears.
    """
    dismiss_modal_if_open(page)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    print("Initiating direct publish flow...")

    # Primary: dropdown arrow next to Publish
    publish_dropdown = (
        page.locator('button[data-test="publish-button-dropdown"]')
        .or_(page.locator('button[data-test="publish-button"]'))
        .or_(page.get_by_role("button", name=re.compile(r"^Publish$", re.I)))
    )

    try:
        publish_dropdown.first.wait_for(state="visible", timeout=15000)
        publish_dropdown.first.click()
        print("Clicked main Publish button/dropdown.")
    except Exception as e:
        print(f"Publish button not found: {e}")
        page.screenshot(path="publish_button_missing.png")
        return

    time.sleep(3)

    # If a dropdown menu appeared, click the "Publish" / "Publish Now" option inside it
    for option_text in ("Publish Now", "Publish Immediately", "Publish"):
        option = page.get_by_role("menuitem", name=re.compile(option_text, re.I))
        if option.count() == 0:
            option = page.get_by_text(option_text, exact=True)
        for i in range(option.count()):
            try:
                if option.nth(i).is_visible():
                    option.nth(i).click()
                    print(f"Clicked dropdown option: '{option_text}'")
                    time.sleep(3)
                    break
            except Exception:
                continue

    # Final confirmation modal (if any)
    for selector in (
        'button:has-text("PUBLISH")',
        'button:has-text("Publish Now")',
        'button:has-text("Go Live")',
    ):
        btn = page.locator(selector).last
        try:
            if btn.is_visible():
                btn.click()
                print(f"Clicked final confirmation: {selector}")
                # FIX #5: Wait for the post-publish URL change instead of blind sleep
                page.wait_for_url(re.compile(r"(posts|blog|config)"), timeout=20000)
                break
        except Exception:
            continue

    time.sleep(3)
    page.screenshot(path="04_after_publish_click.png")
    print("Publish flow complete — screenshot saved.")


# ---------------------------------------------------------------------------
# LOGIN / SESSION
# ---------------------------------------------------------------------------

def do_fresh_login(page, context, email, password):
    """
    Your proven working login sequence — navigates to /config first so
    Squarespace handles the OAuth redirect itself, then fills credentials
    on whatever login page it lands on (account. or login. domain).
    Screenshot saved for GitHub Actions artifact debugging.
    """
    print("Performing fresh Squarespace login...")

    # Go to config — Squarespace redirects to login if not authenticated.
    # This works regardless of whether SQS uses account. or login. subdomain.
    page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")
    time.sleep(5)

    page.screenshot(path="00_login_page.png")
    print(f"  Current URL: {page.url}")

    # Fill credentials — same labels on both squarespace login domains
    page.get_by_label("Email address").fill(email)
    page.get_by_placeholder("Password", exact=True).fill(password)
    page.get_by_role("button", name="Log In").click()

    print("  Credentials submitted — waiting for redirect to /config...")

    # wait_for_url with glob pattern works across account. and login. subdomains
    page.wait_for_url("**/config**", timeout=60000)

    # Persist the session immediately after successful login
    context.storage_state(path=AUTH_STATE_PATH)
    page.screenshot(path="00_login_success.png")
    print("  Login successful — session saved to auth.json.")


# ---------------------------------------------------------------------------
# MAIN ORCHESTRATION
# ---------------------------------------------------------------------------

def run_automation():
    # --- Google Sheets setup ---
    creds   = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    tab     = resolve_sheet_tab(service)

    rows_with_meta = fetch_sheet_rows(service, tab)
    work_items     = select_pending_rows(rows_with_meta)

    if not work_items:
        dump_row_diagnostics(rows_with_meta)
        print("No pending posts found for today or earlier. Exiting.")
        return

    EMAIL    = os.getenv("SQ_EMAIL")
    PASSWORD = os.getenv("SQ_PASSWORD")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context_args = {
            "viewport":   {"width": 1400, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }

        # --- Session / auth ---
        if os.path.exists(AUTH_STATE_PATH):
            print("Session file found — loading storage state...")
            context = browser.new_context(storage_state=AUTH_STATE_PATH, **context_args)
        else:
            print("No session file — will log in fresh.")
            context = browser.new_context(**context_args)

        page = context.new_page()

        # FIX #3: Correct stealth call (works with any playwright-stealth version)
        apply_stealth(page)

        try:
            # --- Verify / refresh session ---
            print("Verifying session...")
            page.goto("https://account.squarespace.com/config/", wait_until="domcontentloaded")
            time.sleep(5)

            session_invalid = (
                "login" in page.url
                or page.locator('input[name="email"]').is_visible()
            )
            if session_invalid:
                do_fresh_login(page, context, EMAIL, PASSWORD)

            page.screenshot(path="01_initial_landing.png")
            print("Session verified — screenshot saved.")

            # --- Process each queued row ---
            for offset, row, post_date in work_items:
                sheet_row = offset + 2
                title     = str(row[0]).strip()
                content   = str(row[1]).strip()

                print(f"\n{'='*60}")
                print(f"Processing row {sheet_row}: {title} ({post_date.isoformat()})")
                print(f"{'='*60}")

                # Mark as Processing immediately (prevents double-posting on re-run)
                update_sheet_status(service, tab, offset, "Processing")

                img_path  = None
                image_url = None

                try:
                    # --- Generate image ---
                    img_path, image_url = generate_image(title)

                    # FIX #1: Navigate directly to the composer — no page-ID, no "Add Post" click
                    print(f"Navigating to direct composer URL: {COMPOSER_URL}")
                    page.goto(COMPOSER_URL, wait_until="domcontentloaded", timeout=60000)

                    # FIX #5: Wait for the iframe properly instead of time.sleep(20)
                    print("Waiting for editor iframe...")
                    frame = wait_for_editor_iframe(page, timeout=60000)

                    page.screenshot(path="02_editor_loaded.png")
                    print("Editor loaded — screenshot saved.")

                    # --- Fill title ---
                    fill_title(frame, title)

                    # --- Fill body ---
                    body_text = format_blog_body(content, post_date)
                    fill_body(frame, body_text)

                    # --- Featured image (FIX #4: post settings, not site settings) ---
                    if img_path and os.path.exists(img_path):
                        upload_featured_image_via_post_settings(page, img_path)

                    # --- Publish directly (no Save Draft) ---
                    run_publish_confirmation_flow(page)

                    # --- Mark success ---
                    update_sheet_status(service, tab, offset, "Posted")
                    print(f"✅  Row {sheet_row} — post '{title}' published successfully.")

                except Exception as row_error:
                    # FIX #7 & #8: Screenshot + mark as Failed so it is retryable
                    print(f"❌  Error on row {sheet_row}: {row_error}")
                    page.screenshot(path=f"error_row_{sheet_row}.png")
                    update_sheet_status(service, tab, offset, "Failed")

                finally:
                    # Always clean up temp image
                    if img_path and os.path.exists(img_path):
                        os.remove(img_path)
                    time.sleep(5)

        except Exception as fatal:
            print(f"Fatal error: {fatal}")
            page.screenshot(path="fatal_error.png")

        finally:
            browser.close()


if __name__ == "__main__":
    run_automation()