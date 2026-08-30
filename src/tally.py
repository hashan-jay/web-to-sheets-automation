from __future__ import annotations

import re
from datetime import datetime

TO_SEND_STATUSES = {"Pending", "Gathered", "Failed", "Preview", "Copying"}
SENT_STATUSES = {"Copied", "Skipped"}
RECORD_RE = re.compile(r"Record:\s*(-?\d+)", re.I)
TOTAL_RE = re.compile(r"Total:\s*(-?[\d,.]+)", re.I)
COMPLETED_STATUS = "COMPLETED"


def local_today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


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


def txn_kind(raw: object) -> str:
    key = str(raw or "").strip().upper()
    if key.startswith("WITHDRAW"):
        return "withdraw"
    return "deposit"


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
