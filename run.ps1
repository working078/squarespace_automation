# Run Squarespace blog automation locally
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

Write-Host "Installing dependencies..."
.\.venv\Scripts\pip.exe install -q -r requirements.txt
.\.venv\Scripts\playwright.exe install chromium | Out-Null

if (Test-Path "auth.json") {
    Write-Host "Slimming auth.json for GitHub secret size..."
    .\.venv\Scripts\python.exe shrink_auth.py
} else {
    Write-Warning "No auth.json — run: python generate_session.py (log in in the browser first)"
}

if (-not (Test-Path "credentials.json") -and -not $env:GOOGLE_CREDENTIALS) {
    Write-Error @"
Missing Google credentials.
  1. Download your service account JSON from Google Cloud Console
  2. Save it as credentials.json in this folder
     OR set env: `$env:GOOGLE_CREDENTIALS = Get-Content credentials.json -Raw
"@
}

Write-Host "Starting automation..."
.\.venv\Scripts\python.exe automation.py
