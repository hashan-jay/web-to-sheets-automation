from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import gspread
from gspread.exceptions import APIError

from src.config import service_account_email
from src.errors import ConfigError
from src.mapper import (
    SHEET_COL_BANK,
    SHEET_COL_COUNT,
    SHEET_COL_DAY,
    SHEET_COL_STATUS,
    date_key,
    is_withdraw,
    pad_sheet_row,
    sheet_tab_name,
    uses_group_d_games,
)
from src.models import Transaction

# GROUP * AUD SEPTEMBER 2026 day tabs have the ledger headings above row 105.
LEDGER_FIRST_DATA_ROW = 105
# Withdrawals sit in a lower block on every linked day tab.
WITHDRAW_FIRST_DATA_ROW = 1024
LEDGER_TITLE_MARKERS = ("group u aud september", "group d aud september")


def normalize_sheet_title(spreadsheet_title: str) -> str:
    return " ".join(
        (spreadsheet_title or "").strip().lower().replace("-", " ").replace("_", " ").split()
    )


def uses_locked_day_column(spreadsheet_title: str) -> bool:
    """September group day tabs lock column A (DAY) on every date tab."""
    return uses_ledger_start(spreadsheet_title) or uses_group_d_games(spreadsheet_title)


def col_letter(index: int) -> str:
    """Convert a 0-based column index to A1 notation (0 -> A)."""
    number = int(index) + 1
    letters = ""
    while number:
        number, rem = divmod(number - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def ledger_skip_columns(
    skip_day_column: bool,
    skip_bank_column: bool = False,
    extra: set[int] | frozenset[int] | None = None,
) -> set[int]:
    skip = set(extra or ())
    if skip_day_column:
        skip.add(SHEET_COL_DAY)
    if skip_bank_column:
        skip.add(SHEET_COL_BANK)
    return skip


def ledger_write_batches(
    rows: list[list[str]],
    start: int,
    skip_columns: set[int] | frozenset[int] | None = None,
    first_data_row: int = 0,
) -> tuple[list[tuple[str, list[list[str]]]], int]:
    """Split A–L into writable column runs that avoid locked cells."""
    values = [pad_sheet_row(row) for row in rows]
    skip = {int(index) for index in (skip_columns or set())}
    if SHEET_COL_BANK in skip:
        for row in values:
            row[SHEET_COL_BANK] = ""
    if first_data_row:
        start = max(start, first_data_row)
    end = start + len(values) - 1
    batches: list[tuple[str, list[list[str]]]] = []
    col = 0
    while col < SHEET_COL_COUNT:
        if col in skip:
            col += 1
            continue
        run_start = col
        while col < SHEET_COL_COUNT and col not in skip:
            col += 1
        run_end = col - 1
        range_name = f"{col_letter(run_start)}{start}:{col_letter(run_end)}{end}"
        batches.append((range_name, [row[run_start : run_end + 1] for row in values]))
    return batches, start


def ledger_write_plan(
    rows: list[list[str]],
    start: int,
    skip_day_column: bool,
    first_data_row: int = 0,
    skip_bank_column: bool = False,
) -> tuple[str, list[list[str]], int]:
    """Build a write that stays out of locked heading cells.

    September ledgers: never write column A, and never write above row 105.
    """
    skip = ledger_skip_columns(skip_day_column, skip_bank_column)
    batches, start = ledger_write_batches(rows, start, skip, first_data_row)
    if not batches:
        return f"B{start}:L{start}", [], start
    return batches[0][0], batches[0][1], start


def uses_ledger_start(spreadsheet_title: str) -> bool:
    title = normalize_sheet_title(spreadsheet_title)
    if "september" in title and (
        "group" in title or "aud" in title or "kaboom" in title
    ):
        return True
    return any(marker in title for marker in LEDGER_TITLE_MARKERS)


UNBOUNDED_ROW = 1_000_000
# Date, description, amount, status, ID, company, player — never skip these if writable.
REQUIRED_WRITE_COLS = (1, 3, 4, 5, 6, 7, 9)


@dataclass(frozen=True)
class LockedBlock:
    start_row: int
    end_row: int
    start_col: int
    end_col: int

    def covers_row(self, row: int) -> bool:
        return self.start_row <= int(row) <= self.end_row

    def covers_col(self, col: int) -> bool:
        return self.start_col <= int(col) <= self.end_col

    def overlaps_rows(self, start: int, end: int) -> bool:
        return not (self.end_row < start or self.start_row > end)


def writer_can_edit_protection(raw: dict, writer_email: str) -> bool:
    if raw.get("warningOnly"):
        return True
    writer = (writer_email or "").strip().lower()
    editors = raw.get("editors") or {}
    users = {str(item).strip().lower() for item in (editors.get("users") or [])}
    return bool(writer) and writer in users


def parse_locked_blocks(
    metadata: dict,
    worksheet_id: int,
    writer_email: str = "",
) -> list[LockedBlock]:
    """Return protections the service account must not write through."""
    wanted = int(worksheet_id)
    blocks: list[LockedBlock] = []
    for sheet in metadata.get("sheets") or []:
        props = sheet.get("properties") or {}
        if int(props.get("sheetId") or -1) != wanted:
            continue
        for raw in sheet.get("protectedRanges") or []:
            if writer_can_edit_protection(raw, writer_email):
                continue
            rng = raw.get("range") or {}
            start_row = rng.get("startRowIndex")
            end_row = rng.get("endRowIndex")
            start_col = rng.get("startColumnIndex")
            end_col = rng.get("endColumnIndex")
            blocks.append(
                LockedBlock(
                    start_row=1 if start_row is None else int(start_row) + 1,
                    end_row=UNBOUNDED_ROW if end_row is None else int(end_row),
                    start_col=0 if start_col is None else int(start_col),
                    end_col=UNBOUNDED_ROW if end_col is None else int(end_col) - 1,
                )
            )
    return blocks


def locked_columns_in_rows(
    blocks: list[LockedBlock], start: int, end: int
) -> set[int]:
    locked: set[int] = set()
    for block in blocks:
        if not block.overlaps_rows(start, end):
            continue
        first = max(block.start_col, 0)
        last = min(block.end_col, SHEET_COL_COUNT - 1)
        for col in range(first, last + 1):
            locked.add(col)
    return locked


def next_unlocked_row(
    blocks: list[LockedBlock],
    start: int,
    n_rows: int = 1,
    last_row: int = 0,
    required: tuple[int, ...] = REQUIRED_WRITE_COLS,
) -> int:
    """First row where required cashbook columns are not protected."""
    row = max(int(start), 1)
    needed = set(required)
    while row < UNBOUNDED_ROW:
        end = row + max(int(n_rows), 1) - 1
        if last_row and row > last_row:
            return 0
        locked = locked_columns_in_rows(blocks, row, end)
        if not (needed & locked):
            return row
        jump_to = row
        for block in blocks:
            if block.covers_row(row) and any(block.covers_col(col) for col in needed):
                jump_to = max(jump_to, block.end_row)
        row = jump_to + 1
    return 0


def writable_append_row(
    id_col: list[str],
    blocks: list[LockedBlock],
    first_data_row: int,
    n_rows: int = 1,
    last_data_row: int = 0,
    required: tuple[int, ...] = REQUIRED_WRITE_COLS,
) -> int:
    """First empty ID row in this deposit or withdrawal block."""
    row = max(int(first_data_row or 1), 1)
    cap = int(last_data_row or 0)
    while row < UNBOUNDED_ROW:
        if cap and row > cap:
            return 0
        value = id_col[row - 1] if row <= len(id_col) else ""
        if str(value).strip().isdigit():
            row += 1
            continue
        unlocked = next_unlocked_row(blocks, row, n_rows, cap, required)
        if unlocked:
            return unlocked
        return row
    return 0


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
        "This app now skips locked column A and heading rows 1–104, and writes "
        "deposits from row 105 and withdrawals from row 1024. If it still fails, "
        "open that date tab as the owner → Data → Protect sheets and ranges. "
        "Keep A:A and C1:AQ104 locked if you want. Unlock the data rows "
        "(C105:L1023 for deposits, C1024:L1044 for withdrawals), or add "
        "sheets-writer@finance-automation-507106.iam.gserviceaccount.com "
        "as an editor of those protected ranges. Then Sync again."
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
            self._ledger_start = LEDGER_FIRST_DATA_ROW
            self._skip_day_column = True
            self._skip_bank_column = False
            self.last_write_start = 0
            self._fallback_title = (worksheet or "").strip()
            self._writer_email = service_account_email(Path(credentials_path))
            self._lock_cache: dict[str, list[LockedBlock]] = {}
            if self._fallback_title:
                self.ws = self.spreadsheet.worksheet(self._fallback_title)
            else:
                self.ws = self.spreadsheet.sheet1
            self._apply_title_layout()
        except APIError as exc:
            raise_if_office_file(exc)
            raise

    def use_day(self, day: str):
        tab = sheet_tab_name(day)
        if not tab:
            if self._fallback_title:
                self.ws = self.spreadsheet.worksheet(self._fallback_title)
                self._apply_tab_layout()
                return self.ws
            raise ConfigError("Cannot choose a Google Sheet tab: the transaction has no date.")
        found = find_day_worksheet(self.spreadsheet, tab)
        if found:
            self.ws = found
            self._apply_tab_layout()
            return self.ws
        title = day_tab_candidates(tab)[0]
        try:
            self.ws = self.spreadsheet.add_worksheet(title=title, rows=2000, cols=16)
        except APIError as exc:
            raise_if_office_file(exc)
            raise
        return self.ws

    def _apply_title_layout(self) -> None:
        self._ledger_start = LEDGER_FIRST_DATA_ROW
        self._skip_day_column = True

    def _apply_tab_layout(self) -> None:
        """Every date tab uses deposits from row 105 and withdrawals from 1024."""
        self._apply_title_layout()

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

    def next_empty_row(self, *, withdraw: bool = False, n_rows: int = 1) -> int:
        ids = self.ws.col_values(7)
        blocks = self._protected_blocks()
        if withdraw:
            return writable_append_row(
                ids,
                blocks,
                first_data_row=WITHDRAW_FIRST_DATA_ROW,
                n_rows=n_rows,
            )
        return writable_append_row(
            ids,
            blocks,
            first_data_row=LEDGER_FIRST_DATA_ROW,
            n_rows=n_rows,
            last_data_row=WITHDRAW_FIRST_DATA_ROW - 1,
        )

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
        return self.write_rows([row], withdraw=row_is_withdraw(row))

    def write_rows(self, rows: list[list[str]], *, withdraw: bool | None = None) -> int:
        if not rows:
            return 0
        if withdraw is None:
            deposits = [row for row in rows if not row_is_withdraw(row)]
            withdraws = [row for row in rows if row_is_withdraw(row)]
            if deposits and withdraws:
                return self.write_rows(deposits, withdraw=False) + self.write_rows(
                    withdraws, withdraw=True
                )
            withdraw = bool(withdraws)
        last_error: Exception | None = None
        skip_day = self._skip_day_column or bool(self._ledger_start)
        skip_bank = bool(withdraw)
        if not skip_bank:
            skip_bank = not any(
                str(pad_sheet_row(row)[SHEET_COL_BANK] or "").strip() for row in rows
            )
        used_safe_plan = False
        for attempt in range(4):
            try:
                start = self.next_empty_row(withdraw=withdraw, n_rows=len(rows))
                if not start:
                    kind = "withdrawal" if withdraw else "deposit"
                    raise ConfigError(
                        f"No empty unlocked {kind} rows left on tab {self.tab_title()}."
                    )
                end = start + len(rows) - 1
                skip = ledger_skip_columns(skip_day, skip_bank)
                locked = locked_columns_in_rows(self._protected_blocks(), start, end)
                skip |= locked - set(REQUIRED_WRITE_COLS)
                batches, start = ledger_write_batches(
                    rows,
                    start,
                    skip,
                    first_data_row=0,
                )
                if not batches:
                    return 0
                self.last_write_start = start
                self._ensure_row_capacity(start + len(rows) - 1)
                self._push_batches(batches)
                self._skip_day_column = skip_day
                self._skip_bank_column = skip_bank
                if skip_day and not self._ledger_start and not withdraw:
                    self._ledger_start = LEDGER_FIRST_DATA_ROW
                return len(rows)
            except APIError as exc:
                office = office_file_error(exc)
                if office:
                    raise office from exc
                locked = protected_range_error(exc)
                if locked and not used_safe_plan:
                    used_safe_plan = True
                    skip_day = True
                    skip_bank = True
                    self._skip_day_column = True
                    self._skip_bank_column = True
                    getattr(self, "_lock_cache", {}).pop(self.tab_title(), None)
                    if not withdraw:
                        self._ledger_start = LEDGER_FIRST_DATA_ROW
                    last_error = locked
                    continue
                if locked:
                    raise locked from exc
                last_error = exc
                if "429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(20 * (attempt + 1))
        raise last_error or RuntimeError("Google Sheets write failed.")

    def _protected_blocks(self) -> list[LockedBlock]:
        if not hasattr(self, "_lock_cache") or self._lock_cache is None:
            self._lock_cache = {}
        title = ""
        try:
            title = self.tab_title()
        except Exception:
            title = ""
        cached = self._lock_cache.get(title)
        if cached is not None:
            return cached
        blocks: list[LockedBlock] = []
        try:
            meta = self.spreadsheet.fetch_sheet_metadata(
                params={
                    "fields": "sheets(properties(sheetId,title),protectedRanges)"
                }
            )
            blocks = parse_locked_blocks(
                meta,
                int(getattr(self.ws, "id", 0) or 0),
                getattr(self, "_writer_email", ""),
            )
        except Exception:
            blocks = []
        self._lock_cache[title] = blocks
        return blocks

    def _push_batches(self, batches: list[tuple[str, list[list[str]]]]) -> None:
        if len(batches) == 1:
            range_name, values = batches[0]
            self.ws.update(
                range_name=range_name,
                values=values,
                value_input_option="USER_ENTERED",
            )
            return
        self.ws.batch_update(
            [{"range": range_name, "values": values} for range_name, values in batches],
            value_input_option="USER_ENTERED",
        )

    def _ensure_row_capacity(self, last_row: int) -> None:
        needed = max(int(last_row) + 50, WITHDRAW_FIRST_DATA_ROW + 50)
        try:
            current = int(getattr(self.ws, "row_count", 0) or 0)
            if current < needed:
                self.ws.resize(rows=needed)
        except Exception:
            pass


def row_is_withdraw(row: list[str]) -> bool:
    status = row[SHEET_COL_STATUS] if len(row) > SHEET_COL_STATUS else ""
    return is_withdraw(status)


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


def looks_like_ledger_tab(day_col: list[str], id_col: list[str]) -> bool:
    """True when this date tab uses the September heading block above row 105."""
    return bool(header_locked_data_row(day_col, id_col))


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
    id_col: list[str],
    header_row: int = 0,
    first_data_row: int = 0,
    last_data_row: int = 0,
) -> int:
    """First row that can take a new transaction ID.

    Deposits stay above the withdrawal block. Withdrawals start at row 1024.
    """
    if first_data_row:
        row = first_data_row
        while True:
            if last_data_row and row > last_data_row:
                return 0
            value = id_col[row - 1] if row <= len(id_col) else ""
            if not str(value).strip().isdigit():
                return max(row, first_data_row)
            row += 1
    first_allowed = header_row + 1 if header_row else 1
    last_data = header_row
    for index, value in enumerate(id_col, start=1):
        if last_data_row and index > last_data_row:
            break
        if str(value).strip().isdigit():
            last_data = index
    start = max(last_data + 1, first_allowed)
    if last_data_row and start > last_data_row:
        return 0
    return start


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
