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


def open_post_editor_from_list(page, title: str) -> bool:
    short = title[:55] if len(title) > 55 else title
    loc = page.get_by_text(short, exact=False).first
    try:
        loc.wait_for(state="visible", timeout=20000)
    except Exception:
        print(f"Post not found: {title!r}")
        return False
    loc.click()
    time.sleep(2)
    # Item options → Edit
    row = loc.locator('xpath=ancestor::div[.//button[@aria-label="Item options"]][1]')
    menu = row.locator('button[aria-label="Item options"]').first
    menu.click()
    time.sleep(1)
    edit = page.get_by_role("menuitem", name=re.compile(r"^edit$", re.I))
    if edit.count() == 0:
        edit = page.get_by_text(re.compile(r"^Edit$", re.I))
    edit.first.click()
    time.sleep(8)
    if page.locator("iframe#sqs-site-frame").count():
        print("Post editor open.")
        return True
    print(f"Editor did not open (url={page.url})")
    return False


def set_publish_date_in_editor(page, post_date: date) -> bool:
    """Set publish date via post settings (7.1: Options → Status) or Published date control."""
    dismiss_modal_if_open(page)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    date_str = post_date.strftime("%d/%m/%Y")
    print(f"Setting publish date to {date_str} ({post_date.isoformat()})")

    # Open post settings (gear or toolbar title)
    for sel in (
        '[aria-label="Settings"]',
        '[data-test="frame-toolbar-title"]',
        'button[aria-label="Post Settings"]',
    ):
        btn = page.locator(sel).first
        if btn.count():
            try:
                btn.click(force=True)
                time.sleep(2)
                break
            except Exception:
                continue

    # Options → Status (Squarespace help center flow)
    for tab_name in ("Options", "Post", "General"):
        tab = page.get_by_role("tab", name=re.compile(tab_name, re.I))
        if tab.count():
            try:
                tab.first.click()
                time.sleep(1)
            except Exception:
                pass

    status = page.get_by_text(re.compile(r"^Status$", re.I))
    if status.count():
        try:
            status.first.click()
            time.sleep(1)
        except Exception:
            pass

    # Click existing date / calendar trigger
    for pattern in (
        r"Published",
        r"\d{1,2}/\d{1,2}/\d{2,4}",
        r"\d{1,2} \w+ \d{4}",
    ):
        el = page.get_by_text(re.compile(pattern, re.I))
        for i in range(min(3, el.count())):
            try:
                t = el.nth(i).inner_text().strip()
                if "Published" in t or re.search(r"\d", t):
                    el.nth(i).click()
                    time.sleep(1)
                    break
            except Exception:
                continue

    # Fill date inputs if visible
    filled = False
    for inp in page.locator('input[type="text"], input[type="date"]').all():
        try:
            if not inp.is_visible():
                continue
            aria = (inp.get_attribute("aria-label") or "").lower()
            ph = (inp.get_attribute("placeholder") or "").lower()
            if "date" in aria or "date" in ph or inp.get_attribute("type") == "date":
                inp.click()
                inp.fill(date_str)
                filled = True
                break
        except Exception:
            continue

    if not filled:
        # Type into focused calendar / date field
        page.keyboard.type(date_str, delay=50)
        filled = True

    time.sleep(1)
    page.keyboard.press("Enter")
    time.sleep(1)

    for label in ("Save", "Apply", "Done"):
        btn = page.get_by_role("button", name=re.compile(f"^{label}$", re.I))
        if btn.count():
            try:
                btn.first.click(force=True)
                time.sleep(2)
                print(f"Clicked {label}.")
                break
            except Exception:
                continue

    page.keyboard.press("Escape")
    time.sleep(1)
    print("Publish date update attempted.")
    return True


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
            if not open_post_editor_from_list(page, args.title):
                page.screenshot(path="fix_date_no_editor.png")
                return 1
            set_publish_date_in_editor(page, post_date)
            page.screenshot(path="fix_date_done.png")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
