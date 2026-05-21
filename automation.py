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
    """Article paragraphs plus a professional branded footer (Metro Express style)."""
    raw = str(content).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw] if raw else [""]
    body = "\n\n".join(paragraphs)
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


def get_editor_content_frame(page):
    iframe = page.query_selector("iframe#sqs-site-frame")
    if not iframe:
        return None
    return iframe.content_frame()


def host_image_publicly(img_path):
    """Upload JPEG to a temporary public URL (backup if Pollinations URL is unavailable)."""
    try:
        with open(img_path, "rb") as f:
            response = requests.post(
                "https://0x0.st",
                files={"file": ("blog-hero.jpg", f, "image/jpeg")},
                timeout=60,
            )
        if response.status_code == 200 and response.text.strip().startswith("http"):
            url = response.text.strip()
            print(f"Image hosted publicly at {url}")
            return url
    except Exception as exc:
        print(f"Public image hosting failed: {exc}")
    return None


def embed_hero_image(page, image_url, img_path, title):
    """
    Insert hero image at the top of the post body inside the editor iframe.
    Uses a public HTTPS URL (primary) or base64 (fallback) — works without (+) buttons.
    """
    inner = get_editor_content_frame(page)
    if not inner:
        raise RuntimeError("Could not access blog editor iframe")

    if not image_url and img_path and os.path.exists(img_path):
        image_url = host_image_publicly(img_path)

    if image_url:
        ok = inner.evaluate(
            """([src, alt]) => {
                const pickBody = () => {
                    for (const ed of document.querySelectorAll('.tiptap.ProseMirror, .ProseMirror')) {
                        if (ed.closest('h1, .entry-title')) continue;
                        return ed;
                    }
                    return document.querySelector('.tiptap.ProseMirror');
                };
                const body = pickBody();
                if (!body) return false;
                body.innerHTML = body.innerHTML.replace(/^\\s*\\/\\s*/g, '').trim();
                const fig = document.createElement('figure');
                fig.className = 'sqs-image sqs-block-alignment-wrapper';
                fig.contentEditable = 'false';
                const img = document.createElement('img');
                img.src = src;
                img.alt = alt || '';
                img.style.cssText = 'width:100%;max-width:100%;height:auto;display:block;margin:0 0 1.5rem;';
                fig.appendChild(img);
                if (body.firstChild) {
                    body.insertBefore(fig, body.firstChild);
                } else {
                    body.appendChild(fig);
                }
                ['input', 'change', 'blur'].forEach((t) =>
                    body.dispatchEvent(new Event(t, { bubbles: true }))
                );
                return !!body.querySelector('img[src]');
            }""",
            [image_url, title],
        )
        if ok:
            print("Hero image embedded in post (public URL).")
            time.sleep(4)
            return True
        print("URL embed returned false; trying base64 fallback...")

    if img_path and os.path.exists(img_path):
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ok = inner.evaluate(
            """([data, alt]) => {
                const pickBody = () => {
                    for (const ed of document.querySelectorAll('.tiptap.ProseMirror, .ProseMirror')) {
                        if (ed.closest('h1, .entry-title')) continue;
                        return ed;
                    }
                    return document.querySelector('.tiptap.ProseMirror');
                };
                const body = pickBody();
                if (!body) return false;
                const fig = document.createElement('figure');
                fig.className = 'sqs-image sqs-block-alignment-wrapper';
                const img = document.createElement('img');
                img.src = 'data:image/jpeg;base64,' + data;
                img.alt = alt || '';
                img.style.cssText = 'width:100%;max-width:100%;height:auto;display:block;margin:0 0 1.5rem;';
                fig.appendChild(img);
                if (body.firstChild) {
                    body.insertBefore(fig, body.firstChild);
                } else {
                    body.appendChild(fig);
                }
                ['input', 'change', 'blur'].forEach((t) =>
                    body.dispatchEvent(new Event(t, { bubbles: true }))
                );
                return true;
            }""",
            [b64, title],
        )
        if ok:
            print("Hero image embedded in post (base64).")
            time.sleep(4)
            return True

    return False


def add_post_image(page, frame, img_path, image_url, title):
    """Add hero image — URL embed first, then (+) upload as last resort."""
    if embed_hero_image(page, image_url, img_path, title):
        return
    if img_path and os.path.exists(img_path):
        try:
            _insert_via_block_plus(page, frame, img_path)
            print("Image uploaded via block (+) menu.")
            return
        except Exception as exc:
            page.screenshot(path="image_upload_failed.png")
            raise RuntimeError(
                "Could not add image to post (URL embed and block upload both failed). "
                f"Last error: {exc}"
            ) from exc
    raise RuntimeError("No image file or URL available for this post.")


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


def _click_image_block_option(page, frame):
    """Choose 'Image' from the Squarespace block picker (iframe or parent UI)."""
    for root in (frame, page):
        for sel in (
            ".sqs-blockbutton",
            ".sqs-blockselector button",
            '[data-block-type="image"]',
            '[data-collection-type-name="image"]',
        ):
            loc = root.locator(sel).filter(has_text=re.compile(r"^image$", re.I))
            if loc.count() > 0:
                loc.first.click(force=True, timeout=5000)
                return True
        for loc in (
            root.get_by_role("button", name=re.compile(r"^image$", re.I)),
            root.get_by_text("Image", exact=True),
        ):
            if loc.count() > 0:
                try:
                    if loc.first.is_visible():
                        loc.first.click(force=True, timeout=5000)
                        return True
                except Exception:
                    continue
    return False


def _upload_after_image_block_added(page, frame, img_path):
    time.sleep(2)
    if set_files_on_any_input(page, frame, img_path):
        time.sleep(12)
        return

    upload_triggers = [
        frame.get_by_text(re.compile(r"upload|computer|browse|device|add images", re.I)),
        page.get_by_text(re.compile(r"upload|computer|browse|device|add images", re.I)),
        frame.locator('[aria-label*="upload" i], [aria-label*="image" i]'),
    ]
    for trigger in upload_triggers:
        if trigger.count() == 0:
            continue
        try:
            with page.expect_file_chooser(timeout=15000) as fc_info:
                trigger.first.click(force=True)
            fc_info.value.set_files(img_path)
            time.sleep(12)
            print("Image attached via file chooser.")
            return
        except Exception:
            continue

    # Classic editor: second (+) inside empty image block
    inner_plus = frame.locator(
        '.sqs-block-image .sqs-blockinsertion-button, '
        '.image-block .sqs-blockinsertion-button, '
        'button[aria-label*="Add" i]'
    )
    if inner_plus.count() > 0:
        inner_plus.first.click(force=True)
        time.sleep(1)
        if set_files_on_any_input(page, frame, img_path):
            time.sleep(12)
            return

    raise RuntimeError("No file upload control found after adding Image block")


PLUS_BUTTON_SELECTORS = (
    ".sqs-blockinsertion-button",
    "button.block-insertion-label",
    ".sqs-layout-insertion-point button",
    '[class*="BlockInsertion"] button',
    '[class*="insertion-point"] button',
)


def _count_plus_buttons(page, frame):
    total = 0
    for root_name, root in (("page", page), ("iframe", frame)):
        for sel in PLUS_BUTTON_SELECTORS:
            n = root.locator(sel).count()
            if n:
                print(f"  {n} match for {sel!r} on {root_name}")
                total += n
    return total


def _reveal_plus_buttons(page, frame):
    """Hover between title and body — (+) controls often live on the parent page overlay."""
    title = frame.locator("h1.entry-title").first
    title.wait_for(state="visible", timeout=15000)
    title.scroll_into_view_if_needed()
    box = title.bounding_box()
    if box:
        for y_offset in (12, 24, 36, 52):
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] + y_offset)
            time.sleep(0.7)
    text_block = frame.locator(".sqs-block-html, .sqs-block-content").first
    if text_block.count() > 0:
        text_block.hover()
        time.sleep(1)


def _all_plus_locators(page, frame):
    locators = []
    for root in (page, frame):
        for sel in PLUS_BUTTON_SELECTORS:
            loc = root.locator(sel)
            if loc.count() > 0:
                locators.append(loc)
    return locators


def _insert_via_block_plus(page, frame, img_path):
    """
    Classic blog editor: hover insert point → (+) → Image → upload.
    (+) buttons are often on the parent page, not inside the iframe DOM.
    """
    print("Trying block insertion (+) between title and body...")
    frame.locator(".sqs-block, .sqs-block-html, .tiptap.ProseMirror").first.wait_for(
        state="attached", timeout=30000
    )
    time.sleep(2)
    _reveal_plus_buttons(page, frame)
    print(f"Insertion controls after hover: {_count_plus_buttons(page, frame)}")

    last_err = "no (+) buttons worked"
    for loc in _all_plus_locators(page, frame):
        count = loc.count()
        for i in range(count):
            btn = loc.nth(i)
            try:
                btn.scroll_into_view_if_needed()
                btn.click(force=True, timeout=8000)
                print(f"Clicked (+) button index {i}")
                time.sleep(1.5)
                if not _click_image_block_option(page, frame):
                    page.keyboard.press("Escape")
                    last_err = f"(+) index {i}: Image not in block picker"
                    continue
                _upload_after_image_block_added(page, frame, img_path)
                return
            except Exception as exc:
                last_err = f"(+) index {i}: {exc}"
                page.keyboard.press("Escape")
                time.sleep(0.5)
    # Last resort: click (+) via JS inside the iframe document (headless-safe).
    iframe_el = page.query_selector("iframe#sqs-site-frame")
    if iframe_el:
        inner = iframe_el.content_frame()
        if inner:
            js_count = inner.evaluate(
                "() => document.querySelectorAll('.sqs-blockinsertion-button').length"
            )
            print(f"JS (+) buttons inside iframe document: {js_count}")
            if js_count:
                inner.evaluate(
                    "() => { const b = document.querySelector('.sqs-blockinsertion-button'); if (b) b.click(); }"
                )
                time.sleep(1.5)
                if _click_image_block_option(page, frame):
                    _upload_after_image_block_added(page, frame, img_path)
                    return

    raise RuntimeError(last_err)


def save_post_draft(page):
    """Persist title/body/image in Squarespace before publishing."""
    for locator in (
        page.get_by_role("button", name=re.compile(r"^SAVE$", re.I)),
        page.locator("button").filter(has_text=re.compile(r"^SAVE$", re.I)),
    ):
        for i in range(locator.count()):
            btn = locator.nth(i)
            try:
                if btn.is_visible():
                    btn.click(timeout=10000)
                    print("Clicked SAVE — draft saved in Squarespace.")
                    time.sleep(6)
                    return True
            except Exception:
                continue
    print("SAVE button not found — continuing without explicit save.")
    return False


def post_still_draft(page):
    """Top bar shows 'Post · Draft' when not live."""
    for pattern in (
        re.compile(r"Post\s*·\s*Draft", re.I),
        re.compile(r"^\s*Draft\s*$", re.I),
    ):
        loc = page.get_by_text(pattern)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    return True
            except Exception:
                continue
    return False


def _visible_publish_buttons(page):
    """All visible toolbar/dialog PUBLISH buttons (not hidden nav text)."""
    indices = []
    for locator in (
        page.get_by_role("button", name=re.compile(r"^PUBLISH$", re.I)),
        page.locator("button").filter(has_text=re.compile(r"^PUBLISH$", re.I)),
    ):
        for i in range(locator.count()):
            try:
                if locator.nth(i).is_visible():
                    indices.append(locator.nth(i))
            except Exception:
                continue
    return indices


def _try_publish_now_soft(page):
    """Click Publish Now if present — optional, never raises."""
    for loc in (
        page.get_by_role("button", name=re.compile(r"Publish\s*Now", re.I)),
        page.locator('button:has-text("Publish Now")'),
        page.locator('button:has-text("PUBLISH NOW")'),
        page.locator('[data-test="publish-now-button"]'),
    ):
        try:
            btn = loc.first
            btn.wait_for(state="visible", timeout=4000)
            btn.click(timeout=8000)
            print("Clicked 'Publish Now'.")
            return True
        except Exception:
            continue
    return False


def upload_featured_image_in_publish_dialog(page, img_path):
    """Upload thumbnail/featured image in the publish dialog (Squarespace CDN)."""
    if not img_path or not os.path.exists(img_path):
        return False
    time.sleep(2)
    if set_files_on_any_input(page, page, img_path):
        print("Featured image uploaded in publish dialog.")
        time.sleep(8)
        return True
    for trigger in (
        page.get_by_text(re.compile(r"upload|add image|replace|computer", re.I)),
        page.locator('[aria-label*="image" i], [aria-label*="upload" i]'),
    ):
        if trigger.count() == 0:
            continue
        try:
            with page.expect_file_chooser(timeout=12000) as fc_info:
                trigger.first.click(force=True)
            fc_info.value.set_files(img_path)
            print("Featured image uploaded via file chooser in publish dialog.")
            time.sleep(8)
            return True
        except Exception:
            continue
    print("No upload control in publish dialog — in-article image may be used only.")
    return False


def publish_post(page, img_path=None, screenshot_suffix=""):
    """
    Publish flow that worked in this repo: PUBLISH → dialog → PUBLISH again (last) or Publish Now.
    Does not hard-fail if 'Publish Now' text is missing (Squarespace UI varies).
    """
    print("Publishing post...")
    dismiss_site_settings_modal(page)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    save_post_draft(page)

    publish_buttons = _visible_publish_buttons(page)
    if not publish_buttons:
        page.screenshot(path=f"publish_btn_missing_{screenshot_suffix}.png")
        raise RuntimeError("No visible PUBLISH button found.")

    publish_buttons[0].click(timeout=10000)
    print("Clicked PUBLISH (opens publish dialog).")
    time.sleep(3)

    upload_featured_image_in_publish_dialog(page, img_path)

    if not _try_publish_now_soft(page):
        if len(publish_buttons) > 1:
            publish_buttons[-1].click(force=True, timeout=10000)
            print("Clicked PUBLISH again (confirmation — last visible button).")
        else:
            extra = _visible_publish_buttons(page)
            if len(extra) > 1:
                extra[-1].click(force=True, timeout=10000)
                print("Clicked PUBLISH confirmation (new button in dialog).")
            elif len(extra) == 1:
                extra[0].click(force=True, timeout=10000)
                print("Clicked single PUBLISH in dialog.")
            else:
                page.get_by_text("PUBLISH", exact=True).last.click(force=True)
                print("Clicked PUBLISH.last (legacy fallback).")

    time.sleep(5)

    for label in ("Done", "Close"):
        btn = page.get_by_role("button", name=label)
        for i in range(btn.count()):
            try:
                if btn.nth(i).is_visible():
                    btn.nth(i).click()
                    print(f"Clicked {label}.")
                    break
            except Exception:
                continue

    time.sleep(6)
    if post_still_draft(page):
        print(
            "WARNING: UI still shows Draft — open Squarespace and click "
            "PUBLISH → Publish Now once manually if needed."
        )
        page.screenshot(path=f"maybe_draft_{screenshot_suffix}.png")
    else:
        print("Post status: no longer Draft in editor.")


def try_publish_menu_post_settings(page, img_path=None, excerpt=None):
    """
    Optional fallback: PUBLISH dropdown → post settings (thumbnail field).
    Never uses data-testid=settings-icon (that opens site Settings).
    """
    dismiss_site_settings_modal(page)
    opened_menu = False
    for dropdown in (
        page.locator('button[data-test="publish-button-dropdown"]'),
        page.locator('button[aria-label*="Publish" i] + button'),
        page.locator('button:has-text("PUBLISH") ~ button').first,
    ):
        if dropdown.count() > 0:
            dropdown.first.click(force=True)
            opened_menu = True
            break
    if not opened_menu:
        publish_btn = page.get_by_role("button", name=re.compile(r"^publish$", re.I))
        if publish_btn.count() > 0:
            publish_btn.first.click(force=True)
            opened_menu = True
    if not opened_menu:
        return False
    time.sleep(1)
    opened = False
    # Never click plain "Settings" — that matches the hidden site nav and hangs.
    for label in ("Post Settings", "SEO", "Social", "Options"):
        item = page.get_by_role("menuitem", name=re.compile(label, re.I))
        if item.count() == 0:
            item = page.get_by_text(label, exact=True)
        for idx in range(item.count()):
            candidate = item.nth(idx)
            try:
                if candidate.is_visible():
                    candidate.click(force=True, timeout=5000)
                    opened = True
                    break
            except Exception:
                continue
        if opened:
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


def generate_image(prompt, filename="blog_image.jpg"):
    print(f"Generating image for: {prompt[:50]}...")
    seed = random.randint(1, 1000000)
    full_prompt = f"Professional transport logistics photography, Australian trucking, {prompt}"
    encoded_prompt = urllib.parse.quote(full_prompt)
    params = f"width=1024&height=1024&seed={seed}&model=flux"
    api_key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    urls = [f"https://image.pollinations.ai/prompt/{encoded_prompt}?{params}"]
    if api_key:
        urls.insert(0, f"https://gen.pollinations.ai/image/{encoded_prompt}?{params}")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for url in urls:
        try:
            response = requests.get(url, timeout=90, headers=headers, allow_redirects=True)
            if response.status_code == 200 and is_valid_jpeg(response.content):
                with open(filename, "wb") as f:
                    f.write(response.content)
                print(f"Image saved ({len(response.content) // 1024} KB) from {url.split('/')[2]}")
                return os.path.abspath(filename), url
            preview = response.content[:40]
            print(f"Bad image from {url.split('/')[2]}: status={response.status_code}, bytes={len(response.content)}, head={preview!r}")
        except Exception as e:
            print(f"Request failed for {url.split('/')[2]}: {e}")
    return None, None

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
            "viewport": {"width": 1400, "height": 900},
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

                    img_path, image_url = generate_image(title)

                    print(f"Navigating to blog list...")
                    page.goto(BASE_URL, wait_until="load", timeout=60000)

                    # Click Add Post (+) in sidebar
                    print("Opening new post editor...")
                    add_button = page.locator('button[aria-label="Add blog post"]').first
                    add_button.wait_for(state="visible", timeout=45000)
                    add_button.click()

                    # Wait for the editor to initialize
                    time.sleep(20)

                    # --- IFRAME HANDLING ---
                    print("Accessing editor iframe...")
                    page.wait_for_selector("iframe#sqs-site-frame", timeout=60000)
                    frame = page.frame_locator("iframe#sqs-site-frame")

                    # Wait for Title inside frame
                    frame.locator("h1.entry-title .ProseMirror").first.wait_for(
                        state="visible", timeout=30000
                    )
                    frame.locator('h1.entry-title .ProseMirror').fill(title)
                    time.sleep(1)

                    body_text = format_blog_body(content, post_date)
                    frame.locator('.tiptap.ProseMirror').last.fill(body_text)
                    time.sleep(1)

                    if img_path or image_url:
                        print("Adding in-article hero image...")
                        try:
                            add_post_image(page, frame, img_path, image_url, title)
                            save_post_draft(page)
                        except Exception as img_exc:
                            print(f"In-article image embed failed (will use publish dialog): {img_exc}")

                    page.screenshot(path=f"before_publish_{offset}.png")

                    publish_post(page, img_path=img_path, screenshot_suffix=str(offset))
                    page.screenshot(path=f"after_publish_{offset}.png")

                    update_sheet_status(service, tab, offset, "Posted")
                    print(f"Success (published live): {title}")
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