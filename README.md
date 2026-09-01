# Finance transaction automation

Copies deposit transactions from the admin dashboard into the Google Sheet, one row per new transaction ID.

## Already installed in this folder

- Python virtual environment: `.venv`
- Playwright, Chromium, `gspread`, Google auth, `python-dotenv`

## What you still configure

1. Copy `.env.example` to `.env`.
2. Set `DASHBOARD_URL`, `DASHBOARD_USERNAME`, and `DASHBOARD_PASSWORD`.
3. Create a Google Cloud service account, enable **Google Sheets API** and **Google Drive API**, and download the JSON key to `credentials/service-account.json`.
4. Share the Google Sheet with the service account email as **Editor**.
5. Set `GOOGLE_SHEET_ID` in `.env` (the long ID in the spreadsheet URL).

If the site uses 2FA, set `MANUAL_LOGIN_SECONDS=90` and complete the prompt in the opened browser on the first run. The login session is saved to `auth_state.json`.

## Run the GUI

```powershell
cd D:\BPO-Projects\finance-automation
.\.venv\Scripts\Activate.ps1
python gui_app.py
```

The window shows the status of each copy. **Start watcher** copies a row as soon as a new notification is inserted into `data/gathering.db` (and also polls the dashboard on the interval you set).

## Deploy as a portable folder (no Python install on the other PC)

Build a self-contained folder that already includes a private Python, pip packages, and Playwright Chromium:

```powershell
cd D:\BPO-Projects\finance-automation
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
```

That creates `dist\FinanceAutomation\`. Zip that folder, copy it to the other PC, unzip it, then:

1. Copy `.env.example` to `.env` and fill in dashboard and Google Sheet values.
2. Copy `credentials\service-account.json` into the `credentials` folder.
3. Double-click `Start Finance Automation.bat`.

The other PC does **not** need Python, pip, or `playwright install`. Chrome is optional; the folder ships its own Chromium. To also copy your current `.env` and credentials into the build (only for machines you control):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1 -IncludeConfig
```

## Run from the command line

```powershell
cd D:\BPO-Projects\finance-automation
.\.venv\Scripts\Activate.ps1
python main.py --dry-run
python main.py
python main.py --pending-only
```

Useful flags:

- `--dry-run` scrape and print rows, do not write to the sheet
- `--date 2026-08-28` override the search start date
- `--limit 5` only process the first 5 transactions
- `--headless` hide the browser

## Sheet columns

Rows are written by category. Only the listed cells are filled; the other columns stay blank.

| | A | B | C | D | E | F | G | H | I | J | K | L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Header | Day | Date | Bank | Description | Amount | Status | ID | Company Owner / Company Name | Company TRF | Player | | Staff |
| Deposit | day | datetime | company bank | player name | amount | Deposit | ID | brand | | username | | staff |
| Withdraw | day | datetime | *(blank, user selects)* | player name | **-amount** | Withdraw | ID | brand | *(blank, user selects)* | username | | staff |

Column G is the duplicate key. Existing IDs are skipped. Withdrawal amounts are stored as negatives so the sheet can sum them. Company TRF is left blank for the dropdown. Bank is filled for deposits and left blank on withdrawals.

Send and Sync write each transaction to the Google Sheet tab named with that day number. A 29th-date row goes to the tab titled `29`.
