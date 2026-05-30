#!/usr/bin/env python3
"""Set Squarespace blog post publish date to match the sheet (column C)."""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime

from automation import (
    AUTH_STATE_PATH,
    active_base_url,
    apply_stealth,
    dismiss_modal_if_open,
    parse_sheet_date,
)

BLOG_PANEL_PATH = "/config/pages/6a0eb6e39567411b2f164a9a"


def open_blog_list(page) -> None:
    page.goto(f"{active_base_url()}{BLOG_PANEL_PATH}", wait_until="domcontentloaded", timeout=90000)
    time.sleep(6)
    dismiss_modal_if_open(page)


def _open_item_options_menu(page, title: str) -> bool:
    short = title[:55] if len(title) > 55 else title
    loc = page.get_by_text(short, exact=False).first
    try:
        loc.wait_for(state="visible", timeout=20000)
    except Exception:
        print(f"Post not found: {title!r}")
        return False
    row = loc.locator('xpath=ancestor::div[.//button[@aria-label="Item options"]][1]')
    menu = row.locator('button[aria-label="Item options"]').first
    menu.scroll_into_view_if_needed()
    menu.click()
    time.sleep(1)
    return True


def open_post_settings_from_list(page, title: str) -> bool:
    """Squarespace 7.1: blog list → Item options → Settings (not Edit)."""
    if not _open_item_options_menu(page, title):
        return False
    settings = page.get_by_role("menuitem", name=re.compile(r"^settings$", re.I))
    if settings.count() == 0:
        settings = page.get_by_text(re.compile(r"^Settings$", re.I))
    try:
        settings.first.click()
        time.sleep(6)
    except Exception as e:
        print(f"Settings menu item not found: {e}")
        page.keyboard.press("Escape")
        return False
    if page.get_by_text(re.compile(r"Blog Post Settings", re.I)).count():
        print("Blog Post Settings panel open.")
        return True
    print(f"Settings panel may not have opened (url={page.url})")
    return False


def _open_options_status_panel(page) -> None:
    """Blog Post Settings: left nav Options → Status (Squarespace 7.1)."""
    clicked_options = False
    modal = (
        page.get_by_role("dialog")
        .filter(has_text=re.compile(r"Blog Post Settings", re.I))
        .or_(page.locator("div").filter(has_text=re.compile(r"Blog Post Settings", re.I)))
    )
    if modal.count():
        opts = modal.first.get_by_text("Options", exact=True)
        if opts.count():
            try:
                opts.first.click()
                time.sleep(2)
                clicked_options = True
                print("Opened Options (scoped to settings modal).")
            except Exception as e:
                print(f"Options click in modal failed: {e}")

    if not clicked_options:
        # Fallback: Options sits directly under Content in the left nav
        content = page.get_by_text("Content", exact=True)
        for i in range(content.count()):
            try:
                el = content.nth(i)
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if not box or box["x"] > 400 or box["y"] < 80:
                    continue
                page.mouse.click(box["x"] + 30, box["y"] + 48)
                time.sleep(2)
                clicked_options = True
                print("Opened Options via click below Content nav item.")
                break
            except Exception:
                continue

    status = page.get_by_text(re.compile(r"^Status$", re.I))
    for i in range(status.count()):
        try:
            el = status.nth(i)
            if not el.is_visible():
                continue
            el.click()
            time.sleep(2)
            print("Opened Status section.")
            break
        except Exception:
            continue


def _pick_calendar_day(page, post_date: date) -> bool:
    """Click day in Squarespace calendar widget (month may need navigation)."""
    day = str(post_date.day)
    # Prefer role=gridcell or button with exact day number
    for sel in (
        page.get_by_role("gridcell", name=re.compile(rf"^{day}$")),
        page.locator(f'button:has-text("{day}")'),
        page.get_by_text(day, exact=True),
    ):
        for i in range(min(sel.count(), 20)):
            try:
                el = sel.nth(i)
                if not el.is_visible():
                    continue
                el.click()
                time.sleep(1)
                print(f"Selected calendar day {day}.")
                return True
            except Exception:
                continue
    return False


def set_publish_date_in_editor(page, post_date: date, *, before_publish: bool = False) -> bool:
    """
    Set publish date (7.1): Options → Status → calendar → Save.
    Works from blog Settings panel or post editor (toolbar title → Options).
    """
    in_post_settings = page.get_by_text(re.compile(r"Blog Post Settings", re.I)).count() > 0
    if not in_post_settings:
        dismiss_modal_if_open(page)
        page.keyboard.press("Escape")
        time.sleep(0.5)

    date_str = post_date.strftime("%d/%m/%Y")
    print(f"Setting publish date to {date_str} ({post_date.isoformat()})")

    # If we're in the editor, open post options like featured-image upload
    if not in_post_settings and page.locator("iframe#sqs-site-frame").count():
        toolbar = page.locator('[data-test="frame-toolbar-title"]')
        if toolbar.count():
            try:
                toolbar.first.click(force=True)
                time.sleep(2)
            except Exception:
                pass

    _open_options_status_panel(page)

    page.screenshot(path="fix_date_options_tab.png")

    # Open date picker: Status row / Published / existing date
    opened_picker = False
    for pattern in (
        r"Date [Pp]ublished",
        r"Publish(?:ed)? (?:on|date)",
        r"^Published$",
        r"\d{1,2}/\d{1,2}/\d{2,4}",
        r"\d{1,2} \w+ \d{4}",
        r"Today",
        r"May \d{1,2}",
    ):
        el = page.get_by_text(re.compile(pattern))
        for i in range(min(5, el.count())):
            try:
                if not el.nth(i).is_visible():
                    continue
                el.nth(i).click()
                time.sleep(1.5)
                opened_picker = True
                break
            except Exception:
                continue
        if opened_picker:
            break

    # Calendar: aria-label e.g. "Friday, May 23, 2026" (toggle day so Squarespace marks dirty)
    other_day = post_date.day - 1 if post_date.day > 1 else post_date.day + 1
    for d in (other_day, post_date.day):
        aria = post_date.strftime("%A, %B ") + f"{d}, {post_date.year}"
        btn = page.get_by_role("button", name=aria)
        if btn.count():
            try:
                btn.first.click()
                time.sleep(1)
                print(f"Calendar: {aria}")
            except Exception:
                pass
        else:
            _pick_calendar_day(page, post_date.replace(day=d))

    time.sleep(1)

    save_labels = (
        ("Save & Publish",)
        if before_publish
        else ("Save & Publish", "Save", "SAVE", "Apply", "Done")
    )
    saved = False
    used_save_and_publish = False
    for label in save_labels:
        for loc in (
            page.get_by_role("button", name=re.compile(f"^{label}$", re.I)),
            page.locator(f'button:has-text("{label}")'),
        ):
            for i in range(loc.count()):
                try:
                    b = loc.nth(i)
                    if not b.is_visible():
                        continue
                    b.click(force=True)
                    time.sleep(4)
                    print(f"Clicked {label}.")
                    saved = True
                    used_save_and_publish = "publish" in label.lower()
                    break
                except Exception:
                    continue
            if saved:
                break
        if saved:
            break

    if not saved:
        if before_publish:
            print("ERROR: Save & Publish not found — post not published (avoids wrong date).")
        else:
            print("WARNING: Save / Save & Publish not clicked — date may not have persisted.")
    elif in_post_settings:
        time.sleep(2)
    if before_publish:
        return used_save_and_publish
    # Existing published post: Settings panel only offers Save (not Save & Publish)
    return saved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="Post title (substring match)")
    parser.add_argument("--date", required=True, help="Date: 23/05/26 or 2026-05-23")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    post_date = parse_sheet_date(args.date)
    if post_date is None:
        try:
            post_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Could not parse date: {args.date!r}")
            return 1

    from pathlib import Path
    if not Path(AUTH_STATE_PATH).exists():
        print(f"Missing {AUTH_STATE_PATH}")
        return 1

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(storage_state=AUTH_STATE_PATH)
        page = ctx.new_page()
        apply_stealth(page)
        try:
            open_blog_list(page)
            if not open_post_settings_from_list(page, args.title):
                page.screenshot(path="fix_date_no_settings.png")
                return 1
            page.screenshot(path="fix_date_before.png")
            ok = set_publish_date_in_editor(page, post_date)
            page.screenshot(path="fix_date_done.png")
            if not ok:
                return 1
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
