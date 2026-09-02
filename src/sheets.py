from __future__ import annotations

import time
from pathlib import Path

import gspread
from gspread.exceptions import APIError

from src.errors import ConfigError
from src.mapper import (
    SHEET_COL_BANK,
    date_key,
    pad_sheet_row,
    sheet_tab_name,
    uses_group_d_games,
)
from src.models import Transaction

# GROUP U AUD SEPTEMBER 2026 day tabs have the ledger headings above row 105.
LEDGER_FIRST_DATA_ROW = 105
LEDGER_TITLE_MARKERS = ("group u aud september", "group d aud september")


def uses_locked_day_column(spreadsheet_title: str) -> bool:
    """GROUP D / Sheet 3 locks column A on every date tab."""
    return uses_group_d_games(spreadsheet_title)


def ledger_write_plan(
    rows: list[list[str]],
    start: int,
    skip_day_column: bool,
    first_data_row: int = 0,
) -> tuple[str, list[list[str]], int]:
    """Build a write that stays out of locked heading cells.

    GROUP D: never write column A, and never write above row 105.
    """
    values = [pad_sheet_row(row) for row in rows]
    for row in values:
        row[SHEET_COL_BANK] = ""
    if first_data_row:
        start = max(start, first_data_row)
    end = start + len(values) - 1
    if skip_day_column:
        return f"B{start}:L{end}", [row[1:] for row in values], start
    return f"A{start}:L{end}", values, start


def uses_ledger_start(spreadsheet_title: str) -> bool:
    title = " ".join(
        (spreadsheet_title or "").strip().lower().replace("-", " ").replace("_", " ").split()
    )
    if "september" in title and ("group u" in title or "group d" in title):
        return True
    return any(marker in title for marker in LEDGER_TITLE_MARKERS)


def day_tab_candidates(day_number: str) -> list[str]:
    number = str(int(day_number))
    names = [number]
    padded = number.zfill(2)
    if padded not in names:
        names.append(padded)
    return names


def find_day_worksheet(spreadsheet, day_number: str):
    wanted = {name.lower() for name in day_tab_candidates(day_number)}
    for worksheet in spreadsheet.worksheets():
        if worksheet.title.strip().lower() in wanted:
            return worksheet
    return None


def office_file_error(exc: Exception) -> ConfigError | None:
    text = str(exc)
    if "must not be an Office file" not in text and "not supported for this document" not in text:
        return None
    return ConfigError(
        "This file is still an Excel (.xlsx) Office file on Google Drive. "
        "This app can only write to a native Google Sheet. "
        "Open the file in Drive → File → Save as Google Sheets, "
        "then paste that new spreadsheet URL into Sheet 1–5 and share it "
        "with the service account as Editor."
    )


def protected_range_error(exc: Exception) -> ConfigError | None:
    text = str(exc)
    if "protected cell" not in text.lower() and "protected sheet" not in text.lower():
        return None
    return ConfigError(
        "Google Sheet tab has protected cells, so the writer cannot add rows. "
        "Open that spreadsheet as the owner → Data → Protect sheets and ranges. "
        "On this date tab, either remove protection from the data rows "
        "(keep the heading/summary rows locked if you want), or add "
        "sheets-writer@finance-automation-507106.iam.gserviceaccount.com "
        "as an editor of the protected range. Then Sync again."
    )


def raise_if_office_file(exc: Exception) -> None:
    mapped = office_file_error(exc) or protected_range_error(exc)
    if mapped:
        raise mapped from exc


class SheetClient:
    def __init__(self, credentials_path: Path, sheet_id: str, worksheet: str = "") -> None:
        client = gspread.service_account(filename=str(credentials_path))
        try:
            self.spreadsheet = client.open_by_key(sheet_id)
            self.sheet_id = sheet_id
            self.slot = 0
            self._ledger_start = (
                LEDGER_FIRST_DATA_ROW if uses_ledger_start(self.spreadsheet.title) else 0
            )
            self._skip_day_column = uses_locked_day_column(self.spreadsheet.title)
            if self._skip_day_column:
                self._ledger_start = LEDGER_FIRST_DATA_ROW
            self.last_write_start = 0
            self._fallback_title = (worksheet or "").strip()
            if self._fallback_title:
                self.ws = self.spreadsheet.worksheet(self._fallback_title)
            else:
                self.ws = self.spreadsheet.sheet1
        except APIError as exc:
            raise_if_office_file(exc)
            raise

    def use_day(self, day: str):
        tab = sheet_tab_name(day)
        if not tab:
            if self._fallback_title:
                self.ws = self.spreadsheet.worksheet(self._fallback_title)
                return self.ws
            raise ConfigError("Cannot choose a Google Sheet tab: the transaction has no date.")
        found = find_day_worksheet(self.spreadsheet, tab)
        if found:
            self.ws = found
            return self.ws
        title = day_tab_candidates(tab)[0]
        try:
            self.ws = self.spreadsheet.add_worksheet(title=title, rows=2000, cols=16)
        except APIError as exc:
            raise_if_office_file(exc)
            raise
        return self.ws

    def tab_title(self) -> str:
        return self.ws.title

    def existing_ids(self) -> set[str]:
        ids, _by_date = self.id_index()
        return ids

    def id_index(self) -> tuple[set[str], dict[str, set[str]]]:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                return index_sheet_ids(self.ws.col_values(2), self.ws.col_values(7))
            except APIError as exc:
                raise_if_office_file(exc)
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets read failed.")

    def next_empty_row(self) -> int:
        ids = self.ws.col_values(7)
        days = self.ws.col_values(1) if not self._ledger_start else []
        start_at = self._ledger_start or header_locked_data_row(days, ids)
        if start_at:
            return next_append_row(ids, first_data_row=start_at)
        return len(ids) + 1

    def clear_bank_names(self) -> int:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                ids = self.ws.col_values(7)
                start, end = bank_clear_range(ids)
                if self._ledger_start:
                    start = max(start, self._ledger_start)
                if not start or start > end:
                    return 0
                blanks = [[""] for _ in range(end - start + 1)]
                self.ws.update(
                    range_name=f"C{start}:C{end}",
                    values=blanks,
                    value_input_option="USER_ENTERED",
                )
                return sum(1 for item in ids if str(item).strip().isdigit())
            except APIError as exc:
                raise_if_office_file(exc)
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets bank-name clear failed.")

    def write_row(self, row: list[str]) -> int:
        return self.write_rows([row])

    def write_rows(self, rows: list[list[str]]) -> int:
        if not rows:
            return 0
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                start = self.next_empty_row()
                range_name, values, start = ledger_write_plan(
                    rows,
                    start,
                    skip_day_column=self._skip_day_column,
                    first_data_row=self._ledger_start,
                )
                self.last_write_start = start
                self.ws.update(
                    range_name=range_name,
                    values=values,
                    value_input_option="USER_ENTERED",
                )
                return len(rows)
            except APIError as exc:
                raise_if_office_file(exc)
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets write failed.")


def _cell(column: list[str], index: int) -> str:
    if index >= len(column):
        return ""
    return str(column[index] or "").strip().lower()


def header_locked_data_row(day_col: list[str], id_col: list[str]) -> int:
    """Return 105 when the DAY/ID heading sits on row 104."""
    header_index = LEDGER_FIRST_DATA_ROW - 2
    day = _cell(day_col, header_index)
    txn_id = _cell(id_col, header_index)
    if day == "day" or txn_id == "id":
        return LEDGER_FIRST_DATA_ROW
    return 0


def find_header_row(day_col: list[str], date_col: list[str], id_col: list[str]) -> int:
    """1-based row of the DAY/DATE/ID heading, or 0 if the tab has no header."""
    length = max(len(day_col), len(date_col), len(id_col), 0)
    for index in range(length):
        day = _cell(day_col, index)
        date = _cell(date_col, index)
        txn_id = _cell(id_col, index)
        if txn_id == "id" and (day == "day" or date == "date"):
            return index + 1
    for index in range(length):
        if _cell(id_col, index) == "id":
            return index + 1
    return 0


def next_append_row(
    id_col: list[str], header_row: int = 0, first_data_row: int = 0
) -> int:
    """First row that can take a new transaction ID.

    GROUP U AUD SEPTEMBER 2026 day tabs always start at row 105.
    Other sheets keep their existing append-after-last-ID behavior.
    """
    if first_data_row:
        row = first_data_row
        while True:
            value = id_col[row - 1] if row <= len(id_col) else ""
            if not str(value).strip().isdigit():
                return max(row, first_data_row)
            row += 1
    first_allowed = header_row + 1 if header_row else 1
    last_data = header_row
    for index, value in enumerate(id_col, start=1):
        if str(value).strip().isdigit():
            last_data = index
    return max(last_data + 1, first_allowed)


def bank_clear_range(ids: list[str]) -> tuple[int, int]:
    rows = [index for index, item in enumerate(ids, start=1) if str(item).strip().isdigit()]
    if not rows:
        return 0, 0
    return rows[0], rows[-1]


def index_sheet_ids(
    datetimes: list[str], ids: list[str]
) -> tuple[set[str], dict[str, set[str]]]:
    all_ids: set[str] = set()
    by_date: dict[str, set[str]] = {}
    length = max(len(datetimes), len(ids))
    for index in range(length):
        txn_id = (ids[index] if index < len(ids) else "").strip()
        if not txn_id.isdigit():
            continue
        all_ids.add(txn_id)
        day = date_key(datetimes[index] if index < len(datetimes) else "")
        by_date.setdefault(day, set()).add(txn_id)
    return all_ids, by_date


def new_rows_only(
    transactions: list[Transaction], existing_ids: set[str]
) -> list[Transaction]:
    seen: set[str] = set()
    unique: list[Transaction] = []
    for txn in transactions:
        if not txn.transaction_id or txn.transaction_id in existing_ids:
            continue
        if txn.transaction_id in seen:
            continue
        seen.add(txn.transaction_id)
        unique.append(txn)
    return unique
