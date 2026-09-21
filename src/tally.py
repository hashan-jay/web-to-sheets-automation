from __future__ import annotations

import math
import re
from datetime import datetime

TO_SEND_STATUSES = {"Pending", "Gathered", "Failed", "Preview", "Copying"}
SENT_STATUSES = {"Copied", "Skipped"}
RECORD_RE = re.compile(r"Record:\s*(-?\d+)", re.I)
TOTAL_RE = re.compile(r"Total:\s*(-?[\d,.]+)", re.I)
COMPLETED_STATUS = "COMPLETED"
STAFF_DEPOSIT_TYPE = "STAFF DEPOSIT"
STAFF_WITHDRAW_TYPE = "STAFF WITHDRAW"
STAFF_TX_TYPES = (STAFF_DEPOSIT_TYPE, STAFF_WITHDRAW_TYPE)


def local_today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def estimated_pages(records: int, per_page: int) -> int:
    if records <= 0 or per_page <= 0:
        return 0
    return max(1, math.ceil(records / per_page))


PAGE_HREF_RE = re.compile(r"page-(\d+)", re.I)


def pager_last_from_hrefs(hrefs: list[str], current: int = 1) -> int:
    nums = [int(match.group(1)) for href in hrefs if (match := PAGE_HREF_RE.search(str(href or "")))]
    if current:
        nums.append(int(current))
    return max(nums) if nums else 0


def pager_bounds(labels: list[str], current: int = 0) -> tuple[int, int]:
    nums = [int(item) for item in labels if str(item).strip().isdigit()]
    last = max(nums) if nums else 0
    now = current if current else (nums[0] if nums else 1)
    return now, last


def pager_finished(current: int, last: int) -> bool:
    """True when the Completed pager is already on its last page."""
    return bool(last) and int(current or 0) >= int(last)


def parse_website_summary(text: str) -> dict[str, int | str]:
    record = RECORD_RE.search(text or "")
    total = TOTAL_RE.search(text or "")
    return {
        "records": int(record.group(1)) if record else 0,
        "total": total.group(1) if total else "",
    }


def parse_amount(raw: object) -> float:
    text = str(raw or "").strip().replace(",", "").replace("$", "").replace(" ", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def format_amount(value: float) -> str:
    return f"{value:,.2f}"


def normalize_tx_type(raw: object) -> str:
    return " ".join(
        str(raw or "").strip().upper().replace("_", " ").replace("-", " ").split()
    )


def is_withdraw_type(raw: object) -> bool:
    return "WITHDRAW" in normalize_tx_type(raw)


def is_staff_tx_type(raw: object) -> bool:
    key = normalize_tx_type(raw)
    if key in STAFF_TX_TYPES:
        return True
    return key.startswith("STAFF") and ("DEPOSIT" in key or "WITHDRAW" in key)


def txn_kind(raw: object) -> str:
    return "withdraw" if is_withdraw_type(raw) else "deposit"


def staff_scrape_types(filter_type: str = "") -> list[str]:
    """STAFF DEPOSIT and/or STAFF WITHDRAW. ACTIVE/blank means both."""
    key = normalize_tx_type(filter_type)
    compact = key.replace(",", " ").replace(" ", "")
    if not compact or compact in {"ACTIVE", "ALL", "BOTH", "STAFF"}:
        return list(STAFF_TX_TYPES)
    wanted: list[str] = []
    if "STAFFDEPOSIT" in compact or compact == "DEPOSIT":
        wanted.append(STAFF_DEPOSIT_TYPE)
    if "STAFFWITHDRAW" in compact or compact in {"WITHDRAW", "WITHDRAWAL"}:
        wanted.append(STAFF_WITHDRAW_TYPE)
    return wanted or list(STAFF_TX_TYPES)


def copy_group(status: object) -> str:
    key = str(status or "").strip().title()
    if key in SENT_STATUSES:
        return "sent"
    if key == "Failed":
        return "failed"
    return "to_send"


def tally_rows(rows: list[dict]) -> dict[str, float | int]:
    count = 0
    total = 0.0
    for row in rows:
        count += 1
        total += parse_amount(row.get("amount"))
    return {"count": count, "amount": total}
