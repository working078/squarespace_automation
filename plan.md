# Squarespace Automation Plan

This project is a Python-based automation system designed to fetch content from a Google Sheet and automatically post it to a Squarespace blog. It runs on a daily schedule using GitHub Actions.

## 1. System Overview

The system automates the following workflow:
1. **Fetch Content**: Retrieves "Pending" blog posts from a Google Spreadsheet.
2. **Login**: Authenticates into Squarespace using Playwright.
3. **Create Post**: Navigates to the blog configuration, creates a new post, and fills in the title and content.
4. **Schedule**: Sets the post to go live at a specific time (default: 07:00 AM).
5. **Update Status**: Marks the post as "Posted" in the Google Sheet.

## 2. Core Components

### `automation.py`
The main execution script. It uses:
- **Google Sheets API**: To read and update post status.
- **Playwright**: To simulate a real user interaction on the Squarespace dashboard (handling login, editor interaction, and scheduling).
- **Environment Variables**: For secure credential management.

### `daily_post.yml` (GitHub Actions)
The deployment engine.
- **Trigger**: Runs automatically every day at 21:00 UTC.
- **Environment**: Sets up Python 3.11 and headless Chromium (Playwright).
- **Secrets**: Securely injects `SQ_EMAIL`, `SQ_PASSWORD`, and `GOOGLE_CREDENTIALS`.
- **Debug Artifacts**: Automatically captures and uploads screenshots if a step fails.

## 3. Data Structure (Google Sheet)
The automation expects a spreadsheet with the following columns:
- **Column A**: Title of the blog post.
- **Column B**: Content/Body of the post.
- **Column C**: Date to be scheduled.
- **Column D**: Status (Processing, Pending, or Posted).

## 4. Current Configuration
- **Target URL**: [Squarespace Blog Config](https://www.squarespace.com/config/pages/6a00f5fd27ce801ca25aa32e)
- **Schedule Time**: 07:00 AM
- **Footer**: Automatically appends a ClickUp booking link for quotes.

## 5. Maintenance & Debugging
- **Screenshots**: In case of failure, check the GitHub Actions "Summary" page for a `debug-screenshots` artifact.
- **Local Testing**: Can be run locally using `python automation.py` (requires `credentials.json` and `.env` setup).
