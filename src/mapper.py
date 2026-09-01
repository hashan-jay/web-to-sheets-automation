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


def to_sheet_row(txn: Transaction, settings: Settings) -> list[str]:
    """Write only the cashbook fields for this category; leave the rest blank.

    A DAY | B DATE | C BANK | D DESCRIPTION | E AMOUNT | F STATUS | G ID |
    H COMPANY OWNER / COMPANY NAME | I COMPANY TRF | J PLAYER | K STAFF
    """
    when = record_local_datetime(txn.datetime, txn.created, txn.processed)
    brand = normalize_brand(txn.brand, settings)
    bank = sheet_bank(txn, settings)
    description = sheet_description(txn)
    player = (txn.username or "").strip()
    staff = settings.default_staff_code
    return [
        day_from_datetime(when),
        when,
        bank,
        description,
        sheet_amount(txn.amount, txn.status),
        sheet_status(txn.status),
        txn.transaction_id,
        brand,
        "",
        player,
        staff,
    ]
