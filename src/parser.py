from __future__ import annotations

import re
from html import unescape

from src.mapper import captured_brand, first_brand_tag
from src.models import Transaction

ID_RE = re.compile(r"#(\d{8,})")
STATUS_RE = re.compile(r"\b(DEPOSIT|WITHDRAW|WITHDRAWAL|UNCLAIM)\b", re.I)
CREATED_RE = re.compile(r"CREATED\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", re.I)
PROCESSED_RE = re.compile(r"PROCESSED\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", re.I)
BRAND_RE = re.compile(r"\b([A-Z0-9]{4,}VIPA|[A-Z0-9]*SPIN[A-Z0-9]*)\b")

ALIASES = {
    "username": "username",
    "name": "name",
    "mobile": "mobile",
    "bankaccountname": "bank_account_name",
    "bankaccountnumber": "bank_account_number",
    "amount": "amount",
    "bank": "bank",
    "method": "method",
    "datetime": "datetime",
    "gateway": "gateway",
    "bankbsb": "bsb",
    "payid": "pay_id",
    "banklock": "bank_lock",
}


ROW_RE = re.compile(r'<tr[^>]*data-id="(\d+)"[^>]*>(.*?)</tr>', re.I | re.S)
COPY_RE = re.compile(
    r'<div class="copy">(.*?)<input[^>]*class="[^"]*hidden[^"]*"[^>]*value="([^"]*)"',
    re.I | re.S,
)
TYPE_RE = re.compile(r'<div class="type[^"]*">\s*([^<]+)', re.I)
BRAND_TAG_RE = re.compile(r'class="[^"]*name-blacklist[^"]*"[^>]*>([^<]+)', re.I)
SPAN_TAG_RE = re.compile(r"<span(?![^>]*\b(?:text|copy|hidden)\b)[^>]*>([^<]+)</span>", re.I)
CREATED_HTML_RE = re.compile(
    r"<b>\s*CREATED\s*</b>\s*<br\s*/?>\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})",
    re.I,
)
PROCESSED_HTML_RE = re.compile(
    r"<b>\s*PROCESSED\s*</b>\s*<br\s*/?>\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")


def parse_transactions_from_html(html: str) -> list[Transaction]:
    rows: list[Transaction] = []
    for match in ROW_RE.finditer(html or ""):
        txn_id, body = match.group(1), match.group(2)
        values: dict[str, str] = {}
        for copy in COPY_RE.finditer(body):
            label = TAG_RE.sub("", copy.group(1)).replace("COPY", "").strip()
            value = TAG_RE.sub("", unescape(copy.group(2))).strip()
            if ":" not in label:
                continue
            key = ALIASES.get(re.sub(r"\s+", "", label.split(":", 1)[0]).lower())
            if key and value:
                values[key] = value
        type_match = TYPE_RE.search(body)
        brand_tags = [tag.strip() for tag in BRAND_TAG_RE.findall(body) if tag.strip()]
        if not brand_tags:
            brand_tags = [tag.strip() for tag in SPAN_TAG_RE.findall(body) if tag.strip()]
        created = CREATED_HTML_RE.search(body)
        processed = PROCESSED_HTML_RE.search(body)
        txn = Transaction(
            transaction_id=txn_id,
            username=values.get("username", ""),
            name=values.get("name", ""),
            mobile=values.get("mobile", ""),
            bank_account_name=values.get("bank_account_name", ""),
            bank_account_number=values.get("bank_account_number", ""),
            amount=values.get("amount", "").replace(",", ""),
            bank=values.get("bank", ""),
            method=values.get("method", ""),
            datetime=values.get("datetime", ""),
            gateway=values.get("gateway", ""),
            status=(type_match.group(1).strip().upper() if type_match else ""),
            created=created.group(1) if created else "",
            processed=processed.group(1) if processed else "",
            brand=first_brand_tag(*brand_tags) or captured_brand(brand_tags[0] if brand_tags else ""),
            bsb=values.get("bsb", ""),
            pay_id=values.get("pay_id", ""),
            bank_lock=values.get("bank_lock", ""),
        )
        if txn.username or txn.amount:
            rows.append(txn)
    return rows


def parse_transactions_from_text(text: str) -> list[Transaction]:
    matches = list(ID_RE.finditer(text or ""))
    collected: dict[str, Transaction] = {}
    for index, match in enumerate(matches):
        start = matches[index - 1].end() if index else max(0, match.start() - 240)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end]
        txn = _parse_chunk(match.group(1), chunk)
        if txn.username or txn.amount:
            collected[txn.transaction_id] = txn
    return list(collected.values())


def _parse_chunk(transaction_id: str, chunk: str) -> Transaction:
    values: dict[str, str] = {}
    for line in chunk.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        key = ALIASES.get(re.sub(r"\s+", "", label).lower())
        value = value.strip()
        if key and value and "<" not in value:
            values[key] = value

    status_match = STATUS_RE.search(chunk)
    created = CREATED_RE.search(chunk)
    processed = PROCESSED_RE.search(chunk)
    brand = BRAND_RE.search(chunk)
    return Transaction(
        transaction_id=transaction_id,
        username=values.get("username", ""),
        name=values.get("name", ""),
        mobile=values.get("mobile", ""),
        bank_account_name=values.get("bank_account_name", ""),
        bank_account_number=values.get("bank_account_number", ""),
        amount=values.get("amount", "").replace(",", ""),
        bank=values.get("bank", ""),
        method=values.get("method", ""),
        datetime=values.get("datetime", ""),
        gateway=values.get("gateway", ""),
        status=(status_match.group(1).upper() if status_match else ""),
        created=created.group(1) if created else "",
        processed=processed.group(1) if processed else "",
        brand=first_brand_tag(brand.group(1) if brand else ""),
        bsb=values.get("bsb", ""),
        pay_id=values.get("pay_id", ""),
        bank_lock=values.get("bank_lock", ""),
    )
