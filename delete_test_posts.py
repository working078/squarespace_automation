#!/usr/bin/env python3
"""Delete duplicate/test blog posts from Squarespace (uses auth.json session)."""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from automation import (
    AUTH_STATE_PATH,
    active_base_url,
    apply_stealth,
    dismiss_modal_if_open,
)


def ensure_logged_in(page) -> None:
    """Light session check — enough to open the blog post list."""
    base = active_base_url()
    page.goto(f"{base}/config/pages", wait_until="domcontentloaded", timeout=90000)
    time.sleep(6)
    if "login.squarespace.com" in page.url or "/authorize" in page.url:
        raise RuntimeError(
            "Squarespace session expired — run: python generate_session.py"
        )

# Wrong bulk publish 23 May 2026 (scheduled run backlog) — keep row 11 Mansfield IBC only.
DEFAULT_TITLES = (
    "Why More Customers in Myrtleford Are Buying Furniture in Melbourne",
    "How IBC Transport Between Melbourne and Albury Usually Works",
    "The Most Common Small Moves Between Melbourne and Mansfield",
    "The Best Way to Transport Steel and Building Products to Benalla",
    "How Winery Equipment Is Transported on Pallets to Albury",
    "How IBC Transport Between Melbourne and Bright Usually Works",
    "How Direct Delivery Services Help Customers in Corowa Avoid Freight Damage",
    "How People in Benalla Are Transporting Overseas Purchases From Melbourne Warehouses",
)


def navigate_to_blog_posts_list(page) -> None:
    base = active_base_url()
    print(f"Opening pages: {base}/config/pages")
    page.goto(f"{base}/config/pages", wait_until="domcontentloaded", timeout=90000)
    time.sleep(6)
    dismiss_modal_if_open(page)

    blog_nav = page.get_by_text("Blog", exact=True)
    blog_nav.first.wait_for(state="visible", timeout=45000)
    blog_nav.first.click()
    time.sleep(5)
    print(f"Blog panel open: {page.url}")


def _item_options_button(page, title: str):
    """Squarespace 7.1 blog list: each row has button aria-label 'Item options'."""
    title_loc = page.get_by_text(title, exact=True).first
    row = title_loc.locator(
        'xpath=ancestor::div[.//button[@aria-label="Item options"]][1]'
    )
    return row.locator('button[aria-label="Item options"]').first


def delete_post_by_title(page, title: str) -> bool:
    """Delete one blog post matching title (handles duplicates)."""
    print(f"\nLooking for: {title!r}")
    dismiss_modal_if_open(page)

    try:
        page.get_by_text(title, exact=True).first.wait_for(state="visible", timeout=15000)
    except Exception:
        print("  Not found — skip.")
        return False

    menu_btn = _item_options_button(page, title)
    try:
        menu_btn.scroll_into_view_if_needed()
        menu_btn.click()
    except Exception as e:
        print(f"  Could not open Item options: {e}")
        page.screenshot(path=f"delete_menu_missing_{abs(hash(title)) % 10000}.png")
        return False

    time.sleep(1)
    delete_item = page.get_by_role("menuitem", name=re.compile(r"delete", re.I))
    if delete_item.count() == 0:
        delete_item = page.get_by_text(re.compile(r"^Delete", re.I))
    try:
        delete_item.first.wait_for(state="visible", timeout=8000)
        delete_item.first.click()
    except Exception:
        print("  Delete menu item not found.")
        page.keyboard.press("Escape")
        return False

    time.sleep(1)
    # Confirmation dialog
    for sel in (
        page.get_by_role("button", name=re.compile(r"^delete$", re.I)),
        page.locator('button:has-text("Delete")'),
    ):
        for i in range(sel.count()):
            try:
                btn = sel.nth(i)
                if btn.is_visible():
                    btn.click()
                    print("  Deleted.")
                    time.sleep(3)
                    dismiss_modal_if_open(page)
                    return True
            except Exception:
                continue

    print("  Confirm delete not found.")
    page.keyboard.press("Escape")
    return False


def delete_all_matching(page, title: str, max_dupes: int = 5) -> int:
    """Delete up to max_dupes posts with the same title."""
    deleted = 0
    for _ in range(max_dupes):
        if delete_post_by_title(page, title):
            deleted += 1
            navigate_to_blog_posts_list(page)
        else:
            break
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete test blog posts from Squarespace")
    parser.add_argument(
        "--title",
        action="append",
        help="Post title to delete (repeat for multiple). Default: known test titles.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List titles only, no browser")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args()

    titles = args.title or list(DEFAULT_TITLES)
    if args.dry_run:
        print("Would delete posts with these titles:")
        for t in titles:
            print(f"  - {t}")
        return 0

    if not Path(AUTH_STATE_PATH).exists():
        print(f"Missing {AUTH_STATE_PATH} — run generate_session.py first.")
        return 1

    from playwright.sync_api import sync_playwright

    headless = not args.headed
    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=AUTH_STATE_PATH)
        page = context.new_page()
        apply_stealth(page)
        try:
            ensure_logged_in(page)
            navigate_to_blog_posts_list(page)
            for title in titles:
                total += delete_all_matching(page, title)
        finally:
            browser.close()

    print(f"\nDone — deleted {total} post(s).")
    return 0 if total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
