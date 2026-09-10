from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from src.config import Settings, persist_env_values
from src.dashboard_api import DashboardClient, dashboard_origin
from src.deposit_bank import _norm
from src.errors import ConfigError
from src.sheets import SheetClient, day_tab_candidates

EventFn = Callable[..., None]
BANKS_SHEET_TITLE = "Banks"
BANK_LIST_PATHS = (
    "/banks/getAllBanks",
    "/bank/getAllBanks",
    "/banks/getAll",
    "/banks/list",
    "/bank/getAll",
)

FETCH_BANKS_JS = r"""
async () => {
  const paths = [
    "/banks/getAllBanks",
    "/bank/getAllBanks",
    "/banks/getAll",
    "/banks/list",
    "/bank/getAll",
  ];
  let token = "";
  try {
    const admin = JSON.parse(localStorage.getItem("ADMIN") || "{}") || {};
    token = String(admin.token || "");
  } catch (err) {}
  const headers = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
  };
  if (token) headers.token = token;
  const body = new URLSearchParams({
    pageIndex: "0",
    pageSize: "200",
    status: "ALL",
  });
  for (const path of paths) {
    try {
      const resp = await fetch(path, {
        method: "POST",
        headers,
        body,
        credentials: "same-origin",
      });
      const text = await resp.text();
      try { return JSON.parse(text); } catch (err) {}
    } catch (err) {}
  }
  return null;
}
"""

EXTRACT_BANKS_JS = r"""
() => {
  const table = document.querySelector("table");
  if (!table) return [];
  const headerRow = table.querySelector("thead tr") || table.querySelector("tr");
  const headers = Array.from((headerRow && headerRow.children) || []).map((el) =>
    String(el.textContent || "").replace(/\s+/g, " ").trim().toLowerCase()
  );
  let bankIdx = headers.findIndex((h) => h.includes("bank name") || h === "bank");
  let accIdx = headers.findIndex((h) => h.includes("account name"));
  let numIdx = headers.findIndex((h) => h.includes("account number"));
  let idIdx = headers.findIndex((h) => h.includes("bank id") || h === "id");
  if (bankIdx < 0) bankIdx = 3;
  if (accIdx < 0) accIdx = 4;
  if (numIdx < 0) numIdx = 5;
  if (idIdx < 0) idIdx = 0;
  const bodyRows = table.querySelectorAll("tbody tr");
  const rows = bodyRows.length ? bodyRows : table.querySelectorAll("tr");
  const out = [];
  for (const tr of rows) {
    const cells = Array.from(tr.querySelectorAll("td")).map((td) =>
      String(td.innerText || "").replace(/\s+/g, " ").trim()
    );
    if (!cells.length) continue;
    const bankName = cells[bankIdx] || "";
    const accountName = cells[accIdx] || "";
    if (!bankName && !accountName) continue;
    if (/^bank name$/i.test(bankName) || /^account name$/i.test(accountName)) continue;
    out.push({
      bank_id: cells[idIdx] || "",
      bank_name: bankName,
      account_name: accountName,
      account_number: cells[numIdx] || "",
    });
  }
  return out;
}
"""


@dataclass(frozen=True)
class WebsiteBank:
    bank_name: str
    account_name: str
    account_number: str = ""
    bank_id: str = ""

    @property
    def label(self) -> str:
        return format_bank_label(self.bank_name, self.account_name)


def format_bank_label(bank_name: object, account_name: object) -> str:
    bank = " ".join(str(bank_name or "").split())
    account = " ".join(str(account_name or "").split())
    if not bank and not account:
        return ""
    if not account:
        return bank
    if not bank:
        return account
    if _norm(bank) in _norm(account):
        return account
    return f"{bank} {account}"


def parse_website_banks(raw: object) -> list[WebsiteBank]:
    rows = _as_bank_rows(raw)
    banks: list[WebsiteBank] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        bank = WebsiteBank(
            bank_name=_cell(row, "bank_name", "bankName", "bank", "name", "gatewayName"),
            account_name=_cell(
                row, "account_name", "accountName", "accName", "account", "holder"
            ),
            account_number=_cell(
                row, "account_number", "accountNumber", "accNumber", "number"
            ),
            bank_id=_cell(row, "bank_id", "bankId", "id"),
        )
        label = bank.label
        key = _norm(label)
        if not label or key in seen:
            continue
        seen.add(key)
        banks.append(bank)
    return banks


def _cell(row: dict, *keys: str) -> str:
    lower = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value not in (None, ""):
            return " ".join(str(value).split())
    return ""


def _as_bank_rows(raw: object) -> list:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    for key in ("banks", "bankList", "records", "items", "list", "rows"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
    data = raw.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return _as_bank_rows(data)
    return []


def scrape_website_banks(
    settings: Settings,
    session=None,
    on_event: EventFn | None = None,
) -> list[WebsiteBank]:
    banks = _fetch_banks_http(settings)
    if banks:
        _emit(on_event, f"Read {len(banks)} bank account(s) from the website Banks API.")
        return banks
    banks = _fetch_banks_browser(settings, session, on_event)
    if banks:
        _emit(on_event, f"Scraped {len(banks)} bank account(s) from the website Banks page.")
    else:
        _emit(on_event, "No bank accounts were found on the website Banks page.")
    return banks


def seed_website_banks(
    settings: Settings,
    on_event: EventFn | None = None,
    session=None,
) -> tuple[str, ...]:
    """Scrape #banks and write those labels into each Google Sheet BANK dropdown."""
    banks = scrape_website_banks(settings, session=session, on_event=on_event)
    labels = tuple(bank.label for bank in banks if bank.label)
    if not labels:
        return tuple(settings.bank_accounts or ())
    settings.bank_accounts = labels
    persist_env_values({"BANK_ACCOUNTS": "|".join(labels)})
    os.environ["BANK_ACCOUNTS"] = "|".join(labels)
    seeded = 0
    for slot, sheet_id in settings.sheet_slots():
        try:
            sheet = SheetClient(
                settings.google_credentials_path,
                sheet_id,
                settings.google_worksheet,
            )
            count = seed_bank_dropdown(sheet, labels)
            seeded += 1
            _emit(
                on_event,
                f"Sheet {slot}: seeded {count} BANK dropdown value(s) from the website Banks page.",
            )
        except (ConfigError, Exception) as exc:
            _emit(on_event, f"Sheet {slot}: could not seed BANK dropdown: {exc}")
    if not seeded:
        _emit(
            on_event,
            "BANK labels were saved for deposit matching, but no Google Sheet dropdown was updated.",
        )
    return labels


def seed_bank_dropdown(sheet: SheetClient, labels: tuple[str, ...] | list[str]) -> int:
    values = [str(item).strip() for item in labels if str(item).strip()]
    if not values:
        return 0
    ws = _upsert_banks_sheet(sheet.spreadsheet, values)
    _apply_bank_validation(sheet, ws)
    return len(values)


def _upsert_banks_sheet(spreadsheet, labels: list[str]):
    try:
        ws = spreadsheet.worksheet(BANKS_SHEET_TITLE)
    except Exception:
        ws = spreadsheet.add_worksheet(
            title=BANKS_SHEET_TITLE,
            rows=max(len(labels) + 20, 80),
            cols=3,
        )
    rows = [["BANK"]] + [[label] for label in labels]
    try:
        ws.clear()
    except Exception:
        pass
    ws.update(
        range_name=f"A1:A{len(rows)}",
        values=rows,
        value_input_option="USER_ENTERED",
    )
    return ws


def _apply_bank_validation(sheet: SheetClient, banks_ws) -> None:
    last = max(int(getattr(banks_ws, "row_count", 0) or 0), 2)
    formula = f"={BANKS_SHEET_TITLE}!$A$2:$A${last}"
    targets = _validation_targets(sheet.spreadsheet)
    if not targets:
        return
    requests = [
        {
            "setDataValidation": {
                "range": {
                    "sheetId": int(worksheet.id),
                    "startRowIndex": 104,
                    "endRowIndex": 2000,
                    "startColumnIndex": 2,
                    "endColumnIndex": 3,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_RANGE",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        }
        for worksheet in targets
    ]
    try:
        sheet.spreadsheet.batch_update({"requests": requests})
    except Exception:
        pass


def _validation_targets(spreadsheet) -> list:
    day_names = {name.lower() for name in _day_titles()}
    targets = []
    for worksheet in spreadsheet.worksheets():
        title = str(worksheet.title or "").strip()
        if title.lower() == BANKS_SHEET_TITLE.lower():
            continue
        if title.lower() in day_names or title.isdigit():
            targets.append(worksheet)
    if targets:
        return targets
    for worksheet in spreadsheet.worksheets():
        title = str(worksheet.title or "").strip()
        if title.lower() != BANKS_SHEET_TITLE.lower():
            return [worksheet]
    return []


def _day_titles() -> list[str]:
    names: list[str] = []
    for day in range(1, 32):
        names.extend(day_tab_candidates(str(day)))
    return names


def _fetch_banks_http(settings: Settings) -> list[WebsiteBank]:
    client = DashboardClient.from_settings(settings)
    if client is None:
        return []
    origin = (getattr(client.session, "origin", "") or "").rstrip("/")
    if not origin:
        return []
    for path in BANK_LIST_PATHS:
        try:
            response = client.http.post(
                origin + path,
                data={"pageIndex": "0", "pageSize": "200", "status": "ALL"},
                timeout=8,
            )
            if getattr(response, "status_code", 400) >= 400:
                continue
            banks = parse_website_banks(response.json())
            if banks:
                return banks
        except Exception:
            continue
    return []


def _fetch_banks_browser(
    settings: Settings,
    session=None,
    on_event: EventFn | None = None,
) -> list[WebsiteBank]:
    origin = dashboard_origin(settings.dashboard_url)
    if not origin:
        return []
    own_browser = session is None
    playwright = None
    page = None
    try:
        if session is not None:
            _browser, _context, page = session.start(settings)
        else:
            from playwright.sync_api import sync_playwright

            from src.scraper import launch_dashboard_page

            playwright = sync_playwright().start()
            _browser, _context, page = launch_dashboard_page(playwright, settings)
        banks = _read_banks_from_page(page)
        return banks
    except Exception as exc:
        _emit(on_event, f"Could not open the website Banks page: {exc}")
        return []
    finally:
        _return_to_transactions(page, settings)
        if own_browser:
            _close_own_browser(page, playwright)


def _read_banks_from_page(page) -> list[WebsiteBank]:
    try:
        raw = page.evaluate(FETCH_BANKS_JS)
        banks = parse_website_banks(raw)
        if banks:
            return banks
    except Exception:
        pass
    _open_banks_page(page)
    collected: dict[str, WebsiteBank] = {}
    for _ in range(25):
        try:
            raw = page.evaluate(EXTRACT_BANKS_JS)
        except Exception:
            raw = []
        for bank in parse_website_banks(raw):
            collected[bank.label] = bank
        if not _advance_banks_page(page):
            break
    return list(collected.values())


def _open_banks_page(page) -> None:
    try:
        page.evaluate("() => { location.hash = '#banks'; }")
        page.wait_for_timeout(800)
    except Exception:
        pass
    try:
        if "banks" not in str(page.evaluate("() => String(location.hash || '')")).lower():
            page.locator("a[href='#banks'], a:has-text('Banks')").first.click(timeout=4000)
            page.wait_for_timeout(800)
    except Exception:
        pass
    try:
        page.evaluate(
            """() => {
              const selects = Array.from(document.querySelectorAll("select"));
              for (const sel of selects) {
                const label = ((sel.name || "") + " " + (sel.id || "") + " " +
                  (sel.parentElement && sel.parentElement.innerText || "")).toUpperCase();
                if (!label.includes("STATUS")) continue;
                const opt = Array.from(sel.options).find((item) =>
                  String(item.text || item.value || "").toUpperCase().includes("ALL")
                );
                if (!opt) continue;
                sel.value = opt.value;
                sel.dispatchEvent(new Event("input", { bubbles: true }));
                sel.dispatchEvent(new Event("change", { bubbles: true }));
                return opt.value;
              }
              return "";
            }"""
        )
    except Exception:
        pass
    try:
        page.wait_for_selector("table td", timeout=12000)
    except Exception:
        page.wait_for_timeout(1200)


def _advance_banks_page(page) -> bool:
    try:
        from src.scraper import _goto_next_page

        return bool(_goto_next_page(page))
    except Exception:
        return False


def _return_to_transactions(page, settings: Settings) -> None:
    if page is None:
        return
    try:
        from src.scraper import _goto_transactions

        _goto_transactions(page, settings)
    except Exception:
        try:
            page.goto(settings.dashboard_url, wait_until="domcontentloaded")
        except Exception:
            pass


def _close_own_browser(page, playwright) -> None:
    context = getattr(page, "context", None) if page is not None else None
    browser = getattr(context, "browser", None) if context is not None else None
    for closer in (context, browser):
        if closer is None:
            continue
        try:
            closer.close()
        except Exception:
            pass
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            pass


def _emit(on_event: EventFn | None, message: str) -> None:
    if on_event:
        on_event({"kind": "log", "message": message})
