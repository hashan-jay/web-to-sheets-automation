from __future__ import annotations

import re

from src.config import Settings
from src.models import Transaction

TAG_RE = re.compile(r"^\[.*?\]\s*")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
FULL_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
LOCAL_DT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(?::(\d{2}))?)?"
)

STATUS_MAP = {
    "DEPOSIT": "Deposit",
    "UNCLAIM": "Unclaim",
    "WITHDRAW": "Withdraw",
    "WITHDRAWAL": "Withdraw",
}

ALLOWED_BRANDS = (
    "POKIESPARK",
    "FUCKSPIN",
    "JOINTMATE",
    "HITMATE88",
    "AUZBETS",
    "WEMETH",
)


def clean_name(value: str) -> str:
    return TAG_RE.sub("", (value or "").strip())


def record_local_datetime(*parts: object) -> str:
    """Return the first local timestamp stated on the record, unchanged."""
    for raw in parts:
        text = str(raw or "").strip()
        match = LOCAL_DT_RE.search(text)
        if not match:
            continue
        date = match.group(1)
        clock = match.group(2)
        if not clock:
            return date
        seconds = match.group(3)
        return f"{date} {clock}:{seconds}" if seconds else f"{date} {clock}"
    return ""


def day_from_datetime(value: str) -> str:
    match = DATE_RE.search(value or "")
    return str(int(match.group(3))) if match else ""


def date_key(raw: object) -> str:
    match = FULL_DATE_RE.search(str(raw or ""))
    return match.group(1) if match else ""


def txn_local_date(txn: Transaction) -> str:
    stamped = date_key((txn.extras or {}).get("tally_date"))
    if stamped:
        return stamped
    return date_key(record_local_datetime(txn.processed, txn.datetime, txn.created))


def normalize_status(value: str) -> str:
    key = (value or "").strip().upper()
    return STATUS_MAP.get(key, value.title() if value else "")


def resolve_brand(*parts: str, default: str = "") -> str:
    """Return the first allow-listed brand found in tags/text, else default.

    Tags such as FUCKSPINVIPC / FUCKSPINVIPA map to FUCKSPIN.
    Labels like NETLOSSN or [JKFCKSPNAU] are ignored.
    """
    hay = " ".join(part.upper() for part in parts if part)
    if not hay:
        return default
    for brand in sorted(ALLOWED_BRANDS, key=len, reverse=True):
        if brand in hay:
            return brand
    return default


def normalize_brand(value: str, settings: Settings) -> str:
    allowed = settings.allowed_brands or ALLOWED_BRANDS
    raw = (value or "").strip()
    mapped = settings.brand_aliases.get(raw.upper())
    if mapped and mapped.upper() in {item.upper() for item in allowed}:
        return mapped
    return resolve_brand(raw, default="")


def is_withdraw(status: object) -> bool:
    return str(status or "").strip().upper().startswith("WITHDRAW")


def sheet_status(status: object) -> str:
    return "Withdraw" if is_withdraw(status) else "Deposit"


def sheet_amount(amount: object, status: object) -> str:
    raw = str(amount or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if raw.startswith("(") and raw.endswith(")"):
        raw = f"-{raw[1:-1]}"
    unsigned = raw.lstrip("+-") or "0"
    if is_withdraw(status):
        return f"-{unsigned}"
    return unsigned


def sheet_bank(txn: Transaction, settings: Settings) -> str:
    return (settings.default_bank_account or txn.bank or "").strip()


def sheet_description(txn: Transaction) -> str:
    return clean_name(txn.bank_account_name or txn.name)


# Fixed A-L cashbook. Blank I and K hold Company TRF / unused so Staff stays in L.
SHEET_COL_COUNT = 12
SHEET_COL_DAY = 0
SHEET_COL_DATE = 1
SHEET_COL_BANK = 2
SHEET_COL_DESCRIPTION = 3
SHEET_COL_AMOUNT = 4
SHEET_COL_STATUS = 5
SHEET_COL_ID = 6
SHEET_COL_COMPANY = 7
SHEET_COL_COMPANY_TRF = 8
SHEET_COL_PLAYER = 9
SHEET_COL_UNUSED = 10
SHEET_COL_STAFF = 11


def empty_sheet_row() -> list[str]:
    return [""] * SHEET_COL_COUNT


def pad_sheet_row(row: list[str]) -> list[str]:
    padded = list(row) + [""] * (SHEET_COL_COUNT - len(row))
    return padded[:SHEET_COL_COUNT]


def to_sheet_row(txn: Transaction, settings: Settings) -> list[str]:
    """Write deposit and withdraw onto the same A-L columns.

    A DAY | B DATE | C BANK | D DESCRIPTION | E AMOUNT | F STATUS | G ID |
    H COMPANY OWNER / COMPANY NAME | I COMPANY TRF | J PLAYER | K *(blank)* | L STAFF
    """
    when = record_local_datetime(txn.datetime, txn.created, txn.processed)
    row = empty_sheet_row()
    row[SHEET_COL_DAY] = day_from_datetime(when)
    row[SHEET_COL_DATE] = when
    row[SHEET_COL_BANK] = sheet_bank(txn, settings)
    row[SHEET_COL_DESCRIPTION] = sheet_description(txn)
    row[SHEET_COL_AMOUNT] = sheet_amount(txn.amount, txn.status)
    row[SHEET_COL_STATUS] = sheet_status(txn.status)
    row[SHEET_COL_ID] = txn.transaction_id
    row[SHEET_COL_COMPANY] = normalize_brand(txn.brand, settings)
    row[SHEET_COL_COMPANY_TRF] = ""
    row[SHEET_COL_PLAYER] = (txn.username or "").strip()
    row[SHEET_COL_UNUSED] = ""
    row[SHEET_COL_STAFF] = settings.default_staff_code
    return row
