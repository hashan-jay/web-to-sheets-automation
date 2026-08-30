from __future__ import annotations

import re

from src.config import Settings
from src.models import Transaction

TAG_RE = re.compile(r"^\[.*?\]\s*")
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
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


def to_sheet_row(txn: Transaction, settings: Settings) -> list[str]:
    display_name = clean_name(txn.bank_account_name or txn.name)
    when = record_local_datetime(txn.datetime, txn.created, txn.processed)
    return [
        day_from_datetime(when),
        when,
        settings.default_bank_account,
        display_name,
        txn.amount,
        normalize_status(txn.status),
        txn.transaction_id,
        normalize_brand(txn.brand, settings),
        txn.bsb,
        txn.username,
        txn.pay_id,
        settings.default_staff_code,
        when,
    ]
