import os
import json
import time
import re
import html as html_module
from pathlib import Path
import base64
import requests
import random
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build


def apply_stealth(page):
    """No-op — playwright_stealth breaks Squarespace config UI in headless mode."""
    return

# ---------------------------------------------------------------------------
# CONFIGURATION (production defaults — override via env for safe testing)
# ---------------------------------------------------------------------------
PRODUCTION_SPREADSHEET_ID = "18c9Ly0omriZ6hUUQQVPs4kRx7j_j46tavLtXHdG2jts"
PRODUCTION_BASE_URL       = "https://coconut-radish-an89.squarespace.com"
# Public blog listing (not /blog — that URL 404s on this site)
PUBLIC_BLOG_URL           = "https://www.triberural.com.au/news-and-updates"
SCOPES                    = ["https://www.googleapis.com/auth/spreadsheets"]

BOOKING_LINK    = "https://forms.clickup.com/90161562352/f/2kz0rgqg-676/WM5FMNFXZQWBKHRIBF"
AUTH_STATE_PATH = "auth.json"
LOCAL_TZ        = ZoneInfo("Australia/Melbourne")
TEST_OUTPUT_DIR = "test_output"


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_dry_run() -> bool:
    return _env_truthy("DRY_RUN")


def dry_run_skip_browser() -> bool:
    return _env_truthy("DRY_RUN_SKIP_BROWSER")


def sheet_writes_enabled() -> bool:
    return not is_dry_run()


def active_spreadsheet_id() -> str:
    return os.getenv("SPREADSHEET_ID", PRODUCTION_SPREADSHEET_ID).strip()


def active_base_url() -> str:
    return os.getenv("BASE_URL", PRODUCTION_BASE_URL).rstrip("/")


def active_composer_url() -> str:
    return f"{active_base_url()}/edit"


def using_production_sheet() -> bool:
    return active_spreadsheet_id() == PRODUCTION_SPREADSHEET_ID


def using_production_site() -> bool:
    return active_base_url().rstrip("/") == PRODUCTION_BASE_URL.rstrip("/")


def validate_run_safety():
    """Block accidental production writes/publish during local testing."""
    if os.getenv("GITHUB_ACTIONS", "").lower() in ("true", "1") and is_dry_run():
        raise RuntimeError(
            "DRY_RUN must not be set in GitHub Actions (production scheduler)."
        )

    if is_dry_run():
        print("=" * 60)
        print("DRY RUN — will NOT publish and will NOT change spreadsheet status")
        print(f"  Spreadsheet: {active_spreadsheet_id()}")
        print(f"  Site:        {active_base_url()}")
        if dry_run_skip_browser():
            print("  Browser:     skipped (offline layer)")
        print("=" * 60)

        if using_production_sheet() and not _env_truthy("ALLOW_PRODUCTION_SHEET_READ"):
            raise RuntimeError(
                "DRY_RUN with the production Google Sheet is blocked.\n"
                "  1. File → Make a copy of the spreadsheet for testing\n"
                "  2. Share the copy with your service account\n"
                "  3. Set SPREADSHEET_ID to the copy's ID in .env.test\n"
                "Or set ALLOW_PRODUCTION_SHEET_READ=1 for read-only diagnostics only "
                "(still no status writes in DRY_RUN)."
            )

        if not dry_run_skip_browser() and using_production_site():
            if not _env_truthy("ALLOW_PRODUCTION_SITE"):
                raise RuntimeError(
                    "DRY_RUN browser tests on the live Tribe site are blocked.\n"
                    "  1. Duplicate the site in Squarespace (Settings → Developer Tools)\n"
                    "  2. Set BASE_URL to the duplicate site's URL in .env.test\n"
                    "  3. Re-run generate_session.py if needed (same login)\n"
                    "Or set ALLOW_PRODUCTION_SITE=1 to fill the live editor without "
                    "publishing (risk: unsaved draft in editor — not recommended)."
                )
    else:
        if using_production_sheet() and not _env_truthy("ALLOW_LIVE_LAYER3"):
            if active_spreadsheet_id() != PRODUCTION_SPREADSHEET_ID:
                pass  # test sheet + live site is OK for layer 3 when explicitly run
        if using_production_sheet() or using_production_site():
            if _env_truthy("ALLOW_LIVE_LAYER3"):
                print(
                    "LIVE LAYER 3 — will publish to live site and update test spreadsheet."
                )
            else:
                print("LIVE RUN — production sheet and/or site may be updated and published.")
        else:
            print(
                f"TEST LIVE RUN — sheet={active_spreadsheet_id()!r} "
                f"site={active_base_url()!r}"
            )


def _headless_default() -> bool:
    """CI/Linux runners have no display; local Windows can use a visible browser."""
    override = os.getenv("HEADLESS", "").strip().lower()
    if override in ("0", "false", "no", "off"):
        return False
    if override in ("1", "true", "yes", "on"):
        return True
    if os.getenv("GITHUB_ACTIONS", "").lower() in ("true", "1"):
        return True
    if os.getenv("CI", "").lower() in ("true", "1"):
        return True
    if os.name != "nt" and not os.getenv("DISPLAY"):
        return True
    return False


HEADLESS = _headless_default()
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


def is_runnable_status(value):
    """Pending = ready; Processing = retry after a failed/interrupted run."""
    s = normalize_status(value)
    return is_pending_status(value) or s == "processing"


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
    meta = service.spreadsheets().get(spreadsheetId=active_spreadsheet_id()).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _fetch_column(service, tab, letter, value_render_option):
    result = service.spreadsheets().values().get(
        spreadsheetId=active_spreadsheet_id(),
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
    """
    Daily production rule: only rows where column C = today (Melbourne) and column D allows run.
    Older Pending rows are never auto-posted (missed days stay manual).
    """
    today = today_local()
    scheduled = os.getenv("SCHEDULED_RUN", "").strip().lower() in ("1", "true", "yes")
    print(f"Today (Australia/Melbourne): {today.isoformat()}")
    if scheduled:
        print("Scheduled daily run — will post at most one row: date=today, status=Pending.")

    today_rows = []
    for offset, row, formatted_date in rows_with_meta:
        sheet_row = offset + 2
        status = row[3]
        post_date = parse_sheet_date(row[2])
        if post_date is None and formatted_date:
            post_date = parse_sheet_date(formatted_date)

        if post_date is None:
            continue
        if post_date > today:
            continue
        if post_date < today:
            if is_pending_status(status) or normalize_status(status) == "processing":
                print(
                    f"Row {sheet_row}: skip — date {post_date.isoformat()} is before today; "
                    "missed days are not auto-posted."
                )
            continue

        # post_date == today
        if scheduled:
            if not is_pending_status(status):
                if normalize_status(status) not in ("posted", ""):
                    print(
                        f"Row {sheet_row}: skip — status {status!r} "
                        f"(scheduled run requires Pending for today)."
                    )
                continue
        elif not is_runnable_status(status):
            continue
        elif normalize_status(status) == "processing" and post_date != today:
            continue

        title_preview = str(row[0])[:60]
        print(
            f"Row {sheet_row}: selected — sheet date {post_date.isoformat()} = today, "
            f"status={normalize_status(status)!r}, title={title_preview!r}..."
        )
        today_rows.append((offset, row, post_date))

    if today_rows:
        print(f"Will publish {len(today_rows)} row(s) for {today.isoformat()}.")
    else:
        print(f"No Pending row with sheet date = today ({today.isoformat()}).")
    return today_rows


def update_sheet_status(service, tab, row_index, status):
    if not sheet_writes_enabled():
        print(f"DRY RUN: skip sheet status update → {status!r}")
        return
    range_name = sheet_range(tab, f"D{row_index + 2}")
    body = {"values": [[status]]}
    service.spreadsheets().values().update(
        spreadsheetId=active_spreadsheet_id(),
        range=range_name,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


# ---------------------------------------------------------------------------
# CONTENT FORMATTING
# ---------------------------------------------------------------------------

_SHEET_CLOSING_RE = re.compile(
    r"^For customers and businesses located in .+?"
    r"demand for practical regional delivery services is expected to remain strong "
    r"across the coming years\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TOWN_FROM_CLOSING_RE = re.compile(
    r"For customers and businesses located in ([^,]+),",
    re.IGNORECASE,
)


def extract_post_town(content: str) -> str | None:
    match = _TOWN_FROM_CLOSING_RE.search(str(content))
    if not match:
        return None
    return match.group(1).strip()


def _strip_sheet_boilerplate_closing(paragraphs: list[str]) -> list[str]:
    if not paragraphs:
        return paragraphs
    last = paragraphs[-1].strip()
    if _SHEET_CLOSING_RE.match(last):
        return paragraphs[:-1]
    return paragraphs


def _paragraphs_to_html(paragraphs: list[str]) -> str:
    return "".join(f"<p>{html_module.escape(p)}</p>" for p in paragraphs)


def _blog_footer_plain(area: str, pub_line: str) -> str:
    return (
        "\n\n"
        "—\n\n"
        "Ready to book freight or a small move?\n\n"
        f"Tribe Rural Logistics supports renters and businesses across {area} with "
        "direct transport, clear communication, and dependable local service.\n\n"
        f"Request a quote online: {BOOKING_LINK}\n\n"
        f"{pub_line}"
    )


def _blog_footer_html(area: str, pub_line: str) -> str:
    tribe = (
        f"Tribe Rural Logistics supports renters and businesses across {area} with "
        "direct transport, clear communication, and dependable local service."
    )
    link = html_module.escape(BOOKING_LINK, quote=True)
    return (
        "<p>—</p>"
        "<p><strong>Ready to book freight or a small move?</strong></p>"
        f"<p>{html_module.escape(tribe)}</p>"
        f'<p><a href="{link}">Request a quote online →</a></p>'
        f"<p>{html_module.escape(pub_line)}</p>"
    )


def format_blog_body(content, post_date=None, title=None, *, rich: bool = False):
    """Sheet body plus footer. Use rich=True for HTML (bold + clickable link in editor)."""
    raw        = str(content).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw] if raw else [""]

    town = extract_post_town(raw)
    if not town and title:
        m = re.search(r"\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+Are\b", str(title))
        if m:
            town = m.group(1).strip()

    paragraphs = _strip_sheet_boilerplate_closing(paragraphs)
    area       = f"{town} and regional Victoria" if town else "regional Victoria"
    published  = post_date.strftime("%d %B %Y") if post_date else ""
    pub_line   = (
        f"Published {published} · Tribe Rural Logistics Pty Ltd · ABN 40 677 940 840"
        if published
        else "Tribe Rural Logistics Pty Ltd · ABN 40 677 940 840"
    )
    if rich:
        return _paragraphs_to_html(paragraphs) + _blog_footer_html(area, pub_line)
    body = "\n\n".join(paragraphs)
    return body + _blog_footer_plain(area, pub_line)


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
    api_key      = (
        os.getenv("POLLINATIONS_API_KEY", "").strip()
        or os.getenv("POLLINATIONS_AI", "").strip()
    )
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


def wait_for_editor_iframe(page, timeout=90000):
    """Wait for the editor iframe to appear and return a frame_locator."""
    page.wait_for_selector("iframe#sqs-site-frame", state="visible", timeout=timeout)
    frame = page.frame_locator("iframe#sqs-site-frame")
    frame.locator(".ProseMirror, .tiptap").first.wait_for(state="visible", timeout=timeout)
    return frame


def navigate_to_blog_composer(page):
    """
    Open a new blog post in the Squarespace editor.
    /edit is a public 404 on this site; use /config/pages → Blog → Add blog post.
    """
    base = active_base_url()
    print("Navigating to blog composer (Pages → Blog → Add post)...")

    page.goto(f"{base}/config/pages", wait_until="domcontentloaded", timeout=90000)
    time.sleep(6)

    opened = False
    for label in ("News and Updates", "Blog"):
        blog_nav = page.get_by_text(label, exact=True)
        if blog_nav.count():
            blog_nav.first.wait_for(state="visible", timeout=45000)
            blog_nav.first.click()
            print(f"Opened blog page in admin: {label!r}")
            opened = True
            break
    if not opened:
        raise RuntimeError(
            "Could not find blog page in Pages panel (look for 'News and Updates' or 'Blog')."
        )
    time.sleep(5)

    add_post = page.locator('[aria-label="Add blog post"]').first
    add_post.wait_for(state="visible", timeout=45000)
    add_post.click()
    time.sleep(10)
    print(f"Blog composer open: {page.url}")


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


def fill_body(frame, body_text, *, rich_html: bool = False):
    """Fill post body into the last ProseMirror editor (not the title one)."""
    body_loc = frame.locator(".tiptap.ProseMirror").last
    body_loc.wait_for(state="visible", timeout=20000)
    body_loc.click()
    time.sleep(0.3)
    if rich_html:
        body_loc.evaluate(
            """(element, html) => {
                element.innerHTML = html;
                element.dispatchEvent(new Event('input', { bubbles: true }));
            }""",
            body_text,
        )
    else:
        body_loc.fill(body_text)
    print("Body filled" + (" (rich HTML)." if rich_html else "."))
    time.sleep(1)


def upload_featured_image_via_post_settings(page, img_path):
    """
    Upload the blog featured/thumbnail image via the post options panel.
    Squarespace 7.1: click the post title in the frame toolbar (e.g. "No Title"),
    then set the file input — not the old post-settings-button.
    """
    if not img_path or not os.path.exists(img_path):
        print("No image file — skipping featured image upload.")
        return False

    print("Opening post options for featured image upload...")
    dismiss_modal_if_open(page)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    opened = False
    toolbar_title = page.locator('[data-test="frame-toolbar-title"]')
    if toolbar_title.count():
        try:
            toolbar_title.first.click(force=True)
            time.sleep(2)
            opened = True
        except Exception as e:
            print(f"Could not open post options via toolbar title: {e}")

    if not opened:
        post_settings_btn = (
            page.locator('[data-test="post-settings-button"]')
            .or_(page.locator('button[aria-label="Post Settings"]'))
            .or_(page.locator('button[aria-label="Options"]'))
            .or_(page.locator('[data-testid="post-settings-button"]'))
        )
        if post_settings_btn.count() == 0:
            print("Post options panel not found — skipping featured image.")
            return False
        post_settings_btn.first.click(force=True)
        time.sleep(3)

    for tab_name in ("Featured Image", "Featured", "Image", "Social Image"):
        tab = page.get_by_role("tab", name=re.compile(tab_name, re.I))
        if tab.count():
            try:
                tab.first.click(force=True)
                time.sleep(1)
                break
            except Exception:
                continue

    file_input = page.locator('input[type="file"]').first
    try:
        file_input.wait_for(state="attached", timeout=15000)
        file_input.set_input_files(img_path)
        page.wait_for_function(
            "() => !document.querySelector('.sqs-upload-progress, [data-uploading=\"true\"]')",
            timeout=45000,
        )
        time.sleep(2)
        print("Featured image uploaded via post options panel.")
    except Exception as e:
        print(f"Featured image upload failed: {e}")
        page.screenshot(path="featured_image_error.png")
        page.keyboard.press("Escape")
        return False

    page.keyboard.press("Escape")
    time.sleep(1)
    for label in ("Done", "Close", "Save"):
        btn = page.get_by_role("button", name=re.compile(f"^{label}$", re.I))
        if btn.count() > 0:
            try:
                btn.first.click(force=True)
                break
            except Exception:
                continue
    time.sleep(1)
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

def restore_auth_from_github_secret() -> bool:
    """Build auth.json from AUTH_JSON_BASE64 or auth_github.txt if present."""
    payload = os.getenv("AUTH_JSON_BASE64", "").strip()
    if not payload:
        for name in ("auth_github.txt", "auth_b64.txt"):
            path = Path(name)
            if path.exists():
                payload = path.read_text(encoding="ascii").strip()
                print(f"Restoring session from {name}...")
                break
    if not payload:
        return False
    from session_utils import decode_github_secret_payload
    AUTH_PATH = Path(AUTH_STATE_PATH)
    AUTH_PATH.write_bytes(decode_github_secret_payload(payload))
    print(f"Restored {AUTH_STATE_PATH} ({AUTH_PATH.stat().st_size} bytes)")
    return True


def try_local_squarespace_login(page, context, email, password) -> bool:
    """Log in with email/password on a local machine (not available on GitHub Actions)."""
    if os.getenv("GITHUB_ACTIONS", "").lower() in ("true", "1"):
        return False
    if not email or not password:
        return False
    print("Attempting local Squarespace login (no auth.json)...")
    page.goto("https://login.squarespace.com/", wait_until="networkidle", timeout=90000)
    time.sleep(4)
    email_loc = page.locator('input[type="email"]').first
    email_loc.wait_for(state="attached", timeout=60000)
    email_loc.fill(email, force=True)
    page.locator('input[type="password"]').first.fill(password, force=True)
    for sel in (
        'button[type="submit"]',
        'button:has-text("Log In")',
        'button:has-text("LOG IN")',
    ):
        btn = page.locator(sel).first
        if btn.count() > 0:
            try:
                btn.click(timeout=5000)
                break
            except Exception:
                continue
    try:
        page.wait_for_url(
            re.compile(r"(account\.squarespace\.com|/config)"),
            timeout=120000,
        )
    except Exception:
        page.screenshot(path="login_failed.png")
        print("Login did not reach dashboard — 2FA or captcha may be required.")
        print("Run: python generate_session.py  (log in manually in the browser)")
        return False
    context.storage_state(path=AUTH_STATE_PATH)
    print(f"Login OK — saved {AUTH_STATE_PATH}")
    return True


def ensure_squarespace_session(page, context, email, password) -> None:
    """Load, restore, or create a valid Squarespace session."""
    if not Path(AUTH_STATE_PATH).exists():
        restore_auth_from_github_secret()
    if not Path(AUTH_STATE_PATH).exists():
        if not try_local_squarespace_login(page, context, email, password):
            raise RuntimeError(
                "No auth.json and login failed.\n"
                "Fix: run generate_session.py, restore_auth.py (from AUTH_JSON_BASE64), "
                "or set SQ_EMAIL + SQ_PASSWORD for local login."
            )
    print("Verifying session...")
    check_url = f"{active_base_url()}/config/pages"
    page.goto(check_url, wait_until="domcontentloaded", timeout=90000)
    time.sleep(6)
    current_url = page.url
    print(f"  Post-load URL: {current_url}")
    page.screenshot(path="01_initial_landing.png")
    if "/authorize" in current_url or "login.squarespace.com" in current_url:
        if try_local_squarespace_login(page, context, email, password):
            page.goto(check_url, wait_until="domcontentloaded", timeout=90000)
            time.sleep(6)
            current_url = page.url
        if "/authorize" in current_url or "login.squarespace.com" in current_url:
            raise RuntimeError(
                "Session expired — re-run generate_session.py or restore AUTH_JSON_BASE64."
            )
    # Confirm the admin UI loaded by checking for any known blog nav label.
    # This site uses "News and Updates"; generic Squarespace sites use "Blog".
    blog_nav_labels = ("News and Updates", "Blog", "Pages")
    admin_loaded = any(
        page.get_by_text(label, exact=True).count() > 0
        for label in blog_nav_labels
    )
    if not admin_loaded:
        raise RuntimeError(
            "Session loaded but site editor did not open. "
            "Re-run generate_session.py while logged into this site."
        )
    print("  Session valid — proceeding to post editor.")


# ---------------------------------------------------------------------------
# OFFLINE TEST LAYER (sheet + formatting + image — no Squarespace)
# ---------------------------------------------------------------------------

def _test_output_path(name: str) -> str:
    os.makedirs(TEST_OUTPUT_DIR, exist_ok=True)
    return os.path.join(TEST_OUTPUT_DIR, name)


def run_offline_test():
    """Layer 1: validate sheet logic and image API without opening Squarespace."""
    validate_run_safety()
    creds = get_credentials()
    service = build("sheets", "v4", credentials=creds)
    tab = resolve_sheet_tab(service)
    rows_with_meta = fetch_sheet_rows(service, tab)
    work_items = select_pending_rows(rows_with_meta)

    if not work_items:
        dump_row_diagnostics(rows_with_meta)
        print("No pending posts for today or earlier. Nothing to preview.")
        return

    limit = max(1, int(os.getenv("TEST_ROW_LIMIT", "1")))
    work_items = work_items[:limit]
    print(f"Offline preview for {len(work_items)} row(s) (TEST_ROW_LIMIT={limit})")

    for offset, row, post_date in work_items:
        sheet_row = offset + 2
        title = str(row[0]).strip()
        content = str(row[1]).strip()
        print(f"\n--- Row {sheet_row}: {title!r} ---")
        body_text = format_blog_body(content, post_date, title=title)
        body_path = _test_output_path(f"row_{sheet_row}_body.md")
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(body_text)
        print(f"  Body preview saved: {body_path}")

        img_name = f"row_{sheet_row}_image.jpg"
        img_path, image_url = generate_image(title, filename=_test_output_path(img_name))
        if img_path:
            print(f"  Image saved: {img_path}")
            if image_url:
                print(f"  Image URL: {image_url}")
        else:
            print("  Image generation failed (post could still run without thumbnail).")

    print("\nOffline layer complete — production sheet unchanged, no site access.")


# ---------------------------------------------------------------------------
# MAIN ORCHESTRATION
# ---------------------------------------------------------------------------

def run_automation():
    validate_run_safety()

    if is_dry_run() and dry_run_skip_browser():
        run_offline_test()
        return

    # --- Google Sheets setup ---
    creds   = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    tab     = resolve_sheet_tab(service)

    rows_with_meta = fetch_sheet_rows(service, tab)
    work_items     = select_pending_rows(rows_with_meta)

    force_row = os.getenv("FORCE_SHEET_ROW", "").strip()
    if os.getenv("SCHEDULED_RUN", "").strip().lower() in ("1", "true", "yes"):
        force_row = ""
    if force_row.isdigit():
        target = int(force_row)
        forced = []
        for offset, row, formatted_date in rows_with_meta:
            if offset + 2 != target:
                continue
            post_date = parse_sheet_date(row[2]) or parse_sheet_date(formatted_date)
            if post_date is None:
                break
            forced.append((offset, row, post_date))
            print(f"FORCE_SHEET_ROW={target} — publishing row {target} only (sheet date {post_date}).")
            break
        work_items = forced

    if not work_items:
        # Check if today's post was already published by an earlier run
        # (e.g. the backup schedule fires after the primary already posted).
        today = today_local()
        already_posted_today = any(
            parse_sheet_date(row[2]) == today
            and normalize_status(row[3]) == "posted"
            for _, row, _ in rows_with_meta
            if row[2]
        )
        if already_posted_today:
            print(
                f"Today's post ({today.isoformat()}) is already Published. "
                "Nothing to do. ✅"
            )
            return  # clean exit — not a failure
        dump_row_diagnostics(rows_with_meta)
        print("No post to publish for today. Exiting.")
        if os.getenv("GITHUB_ACTIONS", "").lower() in ("true", "1"):
            raise SystemExit(
                f"No Pending row with sheet date = today ({today_local().isoformat()}, "
                "Australia/Melbourne). Set column D to Pending for that row."
            )
        return

    limit_raw = os.getenv("TEST_ROW_LIMIT", "").strip()
    if is_dry_run() or limit_raw or os.getenv("GITHUB_ACTIONS", "").lower() in ("true", "1"):
        limit = max(1, int(limit_raw or "1"))
        work_items = work_items[:limit]
        print(f"Processing {len(work_items)} row(s) (limit={limit})")

    EMAIL    = os.getenv("SQ_EMAIL")
    PASSWORD = os.getenv("SQ_PASSWORD")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        print(f"Browser headless={HEADLESS}")

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

        session_verified = False
        try:
            # --- Verify session is valid ---
            # Squarespace blocks GitHub Actions IPs with a blank page.
            # auth.json MUST be pre-generated locally via generate_session.py.
            # If no session file exists, fail immediately with a clear message.
            ensure_squarespace_session(page, context, EMAIL, PASSWORD)
            session_verified = True

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

                    navigate_to_blog_composer(page)
                    print("Waiting for editor iframe...")
                    frame = wait_for_editor_iframe(page, timeout=90000)

                    page.screenshot(path="02_editor_loaded.png")
                    print("Editor loaded — screenshot saved.")

                    # --- Fill title ---
                    fill_title(frame, title)

                    # --- Fill body ---
                    body_text = format_blog_body(
                        content, post_date, title=title, rich=True
                    )
                    fill_body(frame, body_text, rich_html=True)

                    # --- Featured image (FIX #4: post settings, not site settings) ---
                    if img_path and os.path.exists(img_path):
                        upload_featured_image_via_post_settings(page, img_path)

                    published_via_date = False
                    if not is_dry_run():
                        from set_post_publish_date import set_publish_date_in_editor

                        today = today_local()
                        if post_date < today:
                            # Prefer Save & Publish with sheet date; else publish now + fix date workflow
                            published_via_date = set_publish_date_in_editor(
                                page, post_date, before_publish=True
                            )
                            if not published_via_date:
                                print(
                                    f"Row {sheet_row}: Save & Publish not available in editor — "
                                    f"will publish now; run fix_publish_date for {post_date.isoformat()}."
                                )
                        # Today's row: Publish Now is fine (stamp will match sheet date)

                    if is_dry_run():
                        page.screenshot(path=_test_output_path(f"row_{sheet_row}_dry_run_editor.png"))
                        print(
                            f"DRY RUN: editor filled for row {sheet_row} — "
                            "publish skipped; sheet status unchanged."
                        )
                    elif not published_via_date:
                        run_publish_confirmation_flow(page)
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
            try:
                page.screenshot(path="fatal_error.png")
            except Exception:
                pass
            raise SystemExit(1) from fatal

        finally:
            # Save refreshed session cookies so the secret auto-renewal step
            # in the workflow can pick them up. Only save when the session was
            # confirmed valid — avoids persisting an expired/failed state.
            if session_verified:
                try:
                    context.storage_state(path=AUTH_STATE_PATH)
                    print(f"Session cookies refreshed and saved → {AUTH_STATE_PATH}")
                except Exception as _e:
                    print(f"Warning: could not save refreshed session: {_e}")
            browser.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Squarespace blog automation")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Layer 1 only: sheet + body + image (sets DRY_RUN, no browser)",
    )
    args = parser.parse_args()
    if args.offline:
        os.environ.setdefault("DRY_RUN", "1")
        os.environ.setdefault("DRY_RUN_SKIP_BROWSER", "1")
    run_automation()