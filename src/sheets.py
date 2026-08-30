from __future__ import annotations

import time
from pathlib import Path

import gspread
from gspread.exceptions import APIError

from src.models import Transaction


class SheetClient:
    def __init__(self, credentials_path: Path, sheet_id: str, worksheet: str = "") -> None:
        client = gspread.service_account(filename=str(credentials_path))
        spreadsheet = client.open_by_key(sheet_id)
        self.ws = spreadsheet.worksheet(worksheet) if worksheet else spreadsheet.sheet1

    def existing_ids(self) -> set[str]:
        values = self.ws.col_values(7)
        return {item.strip() for item in values if item.strip() and item.strip().isdigit()}

    def next_empty_row(self) -> int:
        ids = self.ws.col_values(7)
        return len(ids) + 1

    def write_row(self, row: list[str]) -> int:
        return self.write_rows([row])

    def write_rows(self, rows: list[list[str]]) -> int:
        if not rows:
            return 0
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                start = self.next_empty_row()
                self.ws.update(
                    range_name=f"A{start}",
                    values=rows,
                    value_input_option="USER_ENTERED",
                )
                return len(rows)
            except APIError as exc:
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets write failed.")


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
