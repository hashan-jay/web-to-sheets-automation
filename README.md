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

| A | B | C | D | E | F | G | H | I | J | K | L | M |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Day | Datetime | Receiving account | Name | Amount | Status | ID | Brand | BSB | Username | PayID | Staff code | Created |

Column G is the duplicate key. Existing IDs are skipped.
