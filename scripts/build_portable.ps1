# Builds dist\FinanceAutomation — a copy-to-another-PC folder with
# a private Python, pip dependencies, Playwright Chromium, and app source.
# The target PC does not need to install Python or Playwright.

param(
    [string]$OutputDir = "",
    [switch]$IncludeConfig
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dist = if ($OutputDir) { $OutputDir } else { Join-Path $Root "dist\FinanceAutomation" }
$Runtime = Join-Path $Dist "runtime"
$Browsers = Join-Path $Dist "ms-playwright"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "Building portable app in $Dist"

if (Test-Path $Dist) {
    Remove-Item -LiteralPath $Dist -Recurse -Force
}
New-Item -ItemType Directory -Path $Dist, $Runtime, (Join-Path $Dist "credentials"), (Join-Path $Dist "data") | Out-Null

function Test-FullPython([string]$Prefix) {
    return (Test-Path (Join-Path $Prefix "python.exe")) -and
        (Test-Path (Join-Path $Prefix "DLLs")) -and
        (
            (Test-Path (Join-Path $Prefix "tcl")) -or
            (Test-Path (Join-Path $Prefix "Lib\tkinter"))
        )
}

function Get-BasePrefix {
    $python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
    $prefix = & $python -c "import sys; print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0 -or -not $prefix) {
        throw "Could not detect the base Python install. Activate .venv first."
    }
    return $prefix.Trim()
}

$basePrefix = Get-BasePrefix
if (-not (Test-FullPython $basePrefix)) {
    throw "The current Python at '$basePrefix' is missing Tkinter. Use a full python.org install, not the embeddable zip."
}

Write-Host "Copying Python runtime from $basePrefix"
& robocopy $basePrefix $Runtime /E /NFL /NDL /NJH /NJS /nc /ns /np /xd "__pycache__" "Doc" "test" "tests" | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "Failed to copy Python runtime (robocopy exit $LASTEXITCODE)."
}

$PortablePython = Join-Path $Runtime "python.exe"
if (-not (Test-Path $PortablePython)) {
    throw "Copied runtime is missing python.exe"
}

Write-Host "Installing pip packages"
& $PortablePython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) {
    throw "ensurepip failed."
}
& $PortablePython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}
& $PortablePython -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed."
}

Write-Host "Installing Playwright Chromium into the portable folder"
$env:PLAYWRIGHT_BROWSERS_PATH = $Browsers
& $PortablePython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "playwright install chromium failed."
}

Write-Host "Copying application source"
Copy-Item (Join-Path $Root "gui_app.py") (Join-Path $Dist "gui_app.py")
Copy-Item (Join-Path $Root "main.py") (Join-Path $Dist "main.py")
Copy-Item (Join-Path $Root "requirements.txt") (Join-Path $Dist "requirements.txt")
Copy-Item (Join-Path $Root ".env.example") (Join-Path $Dist ".env.example")
Copy-Item (Join-Path $Root "src") (Join-Path $Dist "src") -Recurse
Get-ChildItem -Path (Join-Path $Dist "src") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

if ($IncludeConfig) {
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        Copy-Item $envFile (Join-Path $Dist ".env")
    }
    $credSrc = Join-Path $Root "credentials"
    if (Test-Path $credSrc) {
        Copy-Item $credSrc (Join-Path $Dist "credentials") -Recurse -Force
    }
}

$startBat = Join-Path $Dist "Start Finance Automation.bat"
@(
    "@echo off"
    "setlocal"
    "cd /d `"%~dp0`""
    "if not exist `".env`" copy /Y `".env.example`" `".env`" >nul"
    "if not exist `"credentials`" mkdir `"credentials`""
    "if not exist `"data`" mkdir `"data`""
    "set `"PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright`""
    "set `"PYTHONPATH=%~dp0`""
    "set `"PYTHONUTF8=1`""
    "start `"`" `"%~dp0runtime\pythonw.exe`" `"%~dp0gui_app.py`""
) | Set-Content -LiteralPath $startBat -Encoding ASCII

$debugBat = Join-Path $Dist "Start Debug.bat"
@(
    "@echo off"
    "setlocal"
    "cd /d `"%~dp0`""
    "if not exist `".env`" copy /Y `".env.example`" `".env`" >nul"
    "if not exist `"credentials`" mkdir `"credentials`""
    "if not exist `"data`" mkdir `"data`""
    "set `"PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright`""
    "set `"PYTHONPATH=%~dp0`""
    "set `"PYTHONUTF8=1`""
    "`"%~dp0runtime\python.exe`" `"%~dp0gui_app.py`""
    "if errorlevel 1 pause"
) | Set-Content -LiteralPath $debugBat -Encoding ASCII

$readme = Join-Path $Dist "HOW TO RUN.txt"
@(
    "Finance Automation — portable copy"
    ""
    "This folder already includes Python, pip packages, and Playwright Chromium."
    "The other PC does not need to install Python or Playwright."
    ""
    "First-time setup on the new PC"
    "1. Copy .env.example to .env and fill DASHBOARD_URL, login, and GOOGLE_SHEET_ID."
    "2. Put the Google service-account JSON at credentials\service-account.json."
    "3. Double-click Start Finance Automation.bat"
    ""
    "If the window does not open, use Start Debug.bat to see the error."
    "Chrome is optional. This folder ships its own Chromium for scraping."
    "Do not move files out of this folder. Keep runtime, ms-playwright, src, and the .bat files together."
) | Set-Content -LiteralPath $readme -Encoding UTF8

Write-Host ""
Write-Host "Portable app is ready: $Dist"
Write-Host "Zip that folder and copy it to the other PC."
if (-not $IncludeConfig) {
    Write-Host "Remember to add .env and credentials\service-account.json on the other PC."
}
