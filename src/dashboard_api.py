from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from src.config import Settings, normalize_dashboard_url
from src.mapper import captured_brand, first_brand_tag
from src.models import Transaction
from src.tally import COMPLETED_STATUS, format_amount, parse_amount

LIST_PATH = "/transactions/getAllTransactions"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_DT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
PostFn = Callable[[str, dict[str, str]], Any]


@dataclass
class ApiSession:
    origin: str
    token: str = ""
    cookies: list[dict] = field(default_factory=list)


@dataclass
class ApiCapture:
    transactions: list[Transaction] = field(default_factory=list)
    website_records: int = 0
    website_total: str = ""
    pages: int = 0


def dashboard_origin(url: str) -> str:
    parsed = urlparse(normalize_dashboard_url(url))
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def load_api_session(settings: Settings) -> ApiSession | None:
    origin = dashboard_origin(settings.dashboard_url)
    if not origin:
        return None
    path = settings.auth_state_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = ""
    for origin_entry in data.get("origins") or []:
        if str(origin_entry.get("origin") or "").rstrip("/") != origin:
            continue
        for item in origin_entry.get("localStorage") or []:
            if str(item.get("name") or "") != "ADMIN":
                continue
            try:
                admin = json.loads(str(item.get("value") or "{}"))
            except json.JSONDecodeError:
                admin = {}
            token = str(admin.get("token") or "").strip()
    cookies = [cookie for cookie in (data.get("cookies") or []) if isinstance(cookie, dict)]
    if not token and not cookies:
        return None
    return ApiSession(origin=origin, token=token, cookies=cookies)


def list_filter_payload(settings: Settings, page_index: int = 0) -> dict[str, str]:
    day = (settings.filter_date_from or "").strip()
    end = (settings.filter_date_to or day).strip() or day
    status = (settings.filter_status or COMPLETED_STATUS).strip() or COMPLETED_STATUS
    tx_type = (settings.filter_type or "ACTIVE").strip() or "ACTIVE"
    return {
        "pageIndex": str(max(int(page_index), 0)),
        "includeAdmin": "1",
        "background": "0",
        "transactionId": "",
        "name": "",
        "type": tx_type,
        "sDate": f"{day} 00:00:00" if day else "",
        "eDate": f"{end} 23:59:59" if end else "",
        "sCash": "",
        "eCash": "",
        "status": status,
        "agent": "",
        "bankId": "",
        "otherInfo": "",
    }


def looks_like_list_payload(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    rows = raw.get("transactions")
    if not isinstance(rows, list):
        return False
    if "totalCount" in raw or "totalPage" in raw or "totalAmount" in raw:
        return True
    return bool(rows) and isinstance(rows[0], dict) and (
        "id" in rows[0] or "user" in rows[0]
    )


def _text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "null", "undefined"}:
        return ""
    return text


def _parse_json(value: object) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _text(value)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _format_dt(value: object) -> str:
    text = _text(value).replace("T", " ")
    match = _DT_RE.search(text)
    return match.group(1) if match else text


def _amount(value: object) -> str:
    text = _text(value).replace(",", "").replace("$", "")
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _brand_from_row(row: dict, user: dict, details: dict) -> str:
    parts: list[str] = []
    for blob in (user, details, row):
        if not isinstance(blob, dict):
            continue
        for key in (
            "uniqueCode",
            "unique",
            "siteName",
            "site",
            "brand",
            "tag",
            "memberTag",
            "nameBlacklist",
        ):
            parts.append(_text(blob.get(key)))
        tags = blob.get("tags")
        if isinstance(tags, list):
            parts.extend(_text(item) for item in tags)
        elif _text(tags):
            parts.append(_text(tags))
    return first_brand_tag(*parts) or captured_brand(" ".join(part for part in parts if part))


def transaction_from_api(row: dict) -> Transaction:
    user = row.get("user")
    if not isinstance(user, dict):
        user = _parse_json(user)
        if not isinstance(user, dict):
            user = {}
    bank_info = _parse_json(user.get("bank"))
    if isinstance(bank_info, list):
        bank_info = bank_info[0] if bank_info and isinstance(bank_info[0], dict) else {}
    if not isinstance(bank_info, dict):
        bank_info = {}
    details = _parse_json(row.get("details"))
    if not isinstance(details, dict):
        details = {}
    txn_type = _text(row.get("type") or row.get("transactionType")).upper()
    return Transaction(
        transaction_id=_text(row.get("id") or row.get("transactionId")),
        username=_text(user.get("username")),
        name=_text(user.get("name")),
        mobile=_text(user.get("mobile")),
        bank_account_name=_text(
            bank_info.get("accountName")
            or bank_info.get("bankAccountName")
            or bank_info.get("name")
        ),
        bank_account_number=_text(
            bank_info.get("accountNumber")
            or bank_info.get("bankAccountNumber")
            or bank_info.get("number")
        ),
        amount=_amount(row.get("cash") if row.get("cash") not in (None, "") else row.get("amount")),
        bank=_text(bank_info.get("bank") or bank_info.get("code") or bank_info.get("name")),
        method=_text(row.get("method") or details.get("method")),
        datetime=_format_dt(row.get("createdDateTime") or row.get("datetime")),
        gateway=_text(row.get("gateway") or details.get("gateway")),
        status=txn_type,
        created=_format_dt(row.get("createdDateTime")),
        processed=_format_dt(row.get("processedDateTime")),
        brand=_brand_from_row(row, user, details),
        bsb=_text(bank_info.get("bsb") or bank_info.get("BSB") or bank_info.get("bankBsb")),
        pay_id=_text(bank_info.get("payId") or bank_info.get("payID") or bank_info.get("PayID")),
        bank_lock=_text(
            bank_info.get("lock") or bank_info.get("bankLock") or user.get("bankLock")
        ),
    )


def _website_total(raw: object) -> str:
    if raw in (None, ""):
        return ""
    amount = parse_amount(raw)
    if amount == 0 and _text(raw) not in {"0", "0.0", "0.00"}:
        return _text(raw)
    return format_amount(amount)


def fetch_completed(
    post: PostFn,
    settings: Settings,
    limit: int | None = None,
    on_event=None,
    max_pages: int | None = None,
) -> ApiCapture | None:
    first = post(LIST_PATH, list_filter_payload(settings, 0))
    if not looks_like_list_payload(first):
        return None
    total_count = int(first.get("totalCount") or 0)
    total_pages = int(first.get("totalPage") or 0)
    page_limit = total_pages or 1
    if max_pages:
        page_limit = min(page_limit, max(int(max_pages), 1))
    collected: dict[str, Transaction] = {}

    def _take(payload: dict, page_num: int) -> None:
        rows = payload.get("transactions") or []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            txn = transaction_from_api(raw)
            if txn.transaction_id:
                collected[txn.transaction_id] = txn
        if on_event:
            on_event(
                {
                    "kind": "log",
                    "message": (
                        f"HTTP page {page_num}/{page_limit} · "
                        f"{len(collected)} unique of {total_count or '?'} Completed records."
                    ),
                }
            )

    pages_read = 1
    _take(first, 1)
    if total_count and not collected:
        return None
    for page_index in range(1, page_limit):
        if limit and len(collected) >= limit:
            break
        if total_count and len(collected) >= total_count:
            break
        payload = post(LIST_PATH, list_filter_payload(settings, page_index))
        if not looks_like_list_payload(payload):
            break
        before = len(collected)
        pages_read += 1
        _take(payload, page_index + 1)
        if len(collected) == before:
            break

    if total_count and not collected:
        return None
    rows = list(collected.values())
    if limit:
        rows = rows[:limit]
    return ApiCapture(
        transactions=rows,
        website_records=total_count,
        website_total=_website_total(first.get("totalAmount")),
        pages=pages_read,
    )


def _request_headers(origin: str, token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": origin,
        "Referer": f"{origin}/",
        "User-Agent": _USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    if token:
        headers["token"] = token
    return headers


def _with_token(data: dict[str, str], token: str) -> dict[str, str]:
    payload = dict(data)
    if token:
        payload["token"] = token
    return payload


def http_post(session: ApiSession, path: str, data: dict[str, str], timeout: int = 30) -> Any:
    client = requests.Session()
    for cookie in session.cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name:
            continue
        client.cookies.set(
            name,
            value,
            domain=str(cookie.get("domain") or "") or None,
            path=str(cookie.get("path") or "/") or "/",
        )
    url = session.origin.rstrip("/") + path
    try:
        response = client.post(
            url,
            data=_with_token(data, session.token),
            headers=_request_headers(session.origin, session.token),
            timeout=timeout,
        )
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def scrape_via_http(
    settings: Settings,
    limit: int | None = None,
    on_event=None,
) -> ApiCapture | None:
    session = load_api_session(settings)
    if not session:
        return None

    def post(path: str, data: dict[str, str]) -> Any:
        return http_post(session, path, data)

    return fetch_completed(
        post,
        settings,
        limit=limit,
        on_event=on_event,
        max_pages=settings.max_pages,
    )


def scrape_via_playwright_request(
    request,
    origin: str,
    token: str,
    settings: Settings,
    limit: int | None = None,
    on_event=None,
) -> ApiCapture | None:
    if not origin:
        return None

    def post(path: str, data: dict[str, str]) -> Any:
        try:
            response = request.post(
                origin.rstrip("/") + path,
                form=_with_token(data, token),
                headers=_request_headers(origin, token),
                timeout=30000,
            )
        except Exception:
            return None
        if response.status >= 400:
            return None
        try:
            return response.json()
        except Exception:
            return None

    return fetch_completed(
        post,
        settings,
        limit=limit,
        on_event=on_event,
        max_pages=settings.max_pages,
    )
