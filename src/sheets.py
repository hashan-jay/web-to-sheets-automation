from __future__ import annotations

import time
from pathlib import Path

import gspread
from gspread.exceptions import APIError

from src.errors import ConfigError
from src.mapper import date_key, pad_sheet_row, sheet_tab_name
from src.models import Transaction


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


class SheetClient:
    def __init__(self, credentials_path: Path, sheet_id: str, worksheet: str = "") -> None:
        client = gspread.service_account(filename=str(credentials_path))
        self.spreadsheet = client.open_by_key(sheet_id)
        self._fallback_title = (worksheet or "").strip()
        if self._fallback_title:
            self.ws = self.spreadsheet.worksheet(self._fallback_title)
        else:
            self.ws = self.spreadsheet.sheet1

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
        self.ws = self.spreadsheet.add_worksheet(title=title, rows=2000, cols=16)
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
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets read failed.")

    def next_empty_row(self) -> int:
        ids = self.ws.col_values(7)
        return len(ids) + 1

    def clear_bank_names(self) -> int:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                ids = self.ws.col_values(7)
                start, end = bank_clear_range(ids)
                if not start:
                    return 0
                blanks = [[""] for _ in range(end - start + 1)]
                self.ws.update(
                    range_name=f"C{start}:C{end}",
                    values=blanks,
                    value_input_option="USER_ENTERED",
                )
                return sum(1 for item in ids if str(item).strip().isdigit())
            except APIError as exc:
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
        values = [pad_sheet_row(row) for row in rows]
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                start = self.next_empty_row()
                end = start + len(values) - 1
                self.ws.update(
                    range_name=f"A{start}:L{end}",
                    values=values,
                    value_input_option="USER_ENTERED",
                )
                return len(rows)
            except APIError as exc:
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets write failed.")


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
