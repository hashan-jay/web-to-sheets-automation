from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from src.config import Settings, normalize_dashboard_url
from src.deposit_bank import extract_attachment_url
from src.mapper import captured_brand, first_brand_tag
from src.models import Transaction
from src.tally import COMPLETED_STATUS, format_amount, parse_amount, staff_scrape_types

LIST_PATH = "/transactions/getAllTransactions"
READ_ADMIN_TOKEN_JS = """() => {
  try {
    const admin = JSON.parse(localStorage.getItem("ADMIN") || "{}") || {};
    return String(admin.token || "");
  } catch (err) {
    return "";
  }
}"""
LIVE_POST_JS = """
async (data) => {
  const params = data || {};
  let token = "";
  try {
    const admin = JSON.parse(localStorage.getItem("ADMIN") || "{}") || {};
    token = String(admin.token || "");
  } catch (err) {}
  const headers = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
  };
  if (token) headers.token = token;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const resp = await fetch("/transactions/getAllTransactions", {
      method: "POST",
      headers,
      body: new URLSearchParams(params),
      credentials: "same-origin",
      signal: controller.signal,
    });
    const text = await resp.text();
    try { return JSON.parse(text); }
    catch (err) { return { error: "not-json", status: resp.status }; }
  } catch (err) {
    return { error: String((err && err.name) || err), status: 0 };
  } finally {
    clearTimeout(timer);
  }
}
"""
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


def list_filter_payload(
    settings: Settings, page_index: int = 0, tx_type: str = ""
) -> dict[str, str]:
    day = (settings.filter_date_from or "").strip()
    end = (settings.filter_date_to or day).strip() or day
    status = (settings.filter_status or COMPLETED_STATUS).strip() or COMPLETED_STATUS
    if "COMPLETED" not in status.upper():
        status = COMPLETED_STATUS
    chosen = (tx_type or "").strip()
    if not chosen:
        types = staff_scrape_types(settings.filter_type)
        chosen = types[0] if types else "STAFF DEPOSIT"
    return {
        "pageIndex": str(max(int(page_index), 0)),
        "includeAdmin": "1",
        "background": "0",
        "transactionId": "",
        "name": "",
        "type": chosen,
        "sDate": f"{day} 00:00:00" if day else "",
        "eDate": f"{end} 23:59:59" if end else "",
        "sCash": "",
        "eCash": "",
        "status": status,
        "agent": "",
        "bankId": "",
        "otherInfo": "",
    }


def unwrap_list_payload(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("error") and not isinstance(raw.get("transactions"), list):
        return None
    if isinstance(raw.get("transactions"), list):
        return raw
    for key in ("data", "result", "payload"):
        inner = raw.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("transactions"), list):
            return inner
    return None


def looks_like_list_payload(raw: object) -> bool:
    return unwrap_list_payload(raw) is not None


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
    attachment = extract_attachment_url(
        {
            "attachment": row.get("attachment") or details.get("attachment"),
            "attachments": row.get("attachments") or details.get("attachments"),
            "receipt": row.get("receipt") or details.get("receipt"),
            "image": row.get("image") or details.get("image"),
            "file": row.get("file") or details.get("file"),
            "proof": row.get("proof") or details.get("proof"),
            "details": details,
        }
    )
    extras = {"attachment": attachment} if attachment else {}
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
        attachment=attachment,
        extras=extras,
    )


def _website_total(raw: object) -> str:
    if raw in (None, ""):
        return ""
    amount = parse_amount(raw)
    if amount == 0 and _text(raw) not in {"0", "0.0", "0.00"}:
        return _text(raw)
    return format_amount(amount)


def _fetch_completed_type(
    post: PostFn,
    settings: Settings,
    tx_type: str,
    limit: int | None = None,
    on_event=None,
    max_pages: int | None = None,
    known_ids: set[str] | None = None,
    catch_up: bool = True,
    quiet: bool = False,
    expect_new: int | None = None,
) -> ApiCapture | None:
    first = unwrap_list_payload(post(LIST_PATH, list_filter_payload(settings, 0, tx_type)))
    if not first:
        return None
    total_count = int(first.get("totalCount") or 0)
    total_pages = int(first.get("totalPage") or 0)
    page_limit = total_pages or 1
    if max_pages:
        page_limit = min(page_limit, max(int(max_pages), 1))
    if not catch_up:
        page_limit = min(page_limit, 4)
    collected: dict[str, Transaction] = {}
    seen = set(known_ids or ())

    def _take(payload: dict, page_num: int) -> None:
        rows = payload.get("transactions") or []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            txn = transaction_from_api(raw)
            if not txn.status:
                txn.status = tx_type
            if not txn.transaction_id or txn.transaction_id in collected:
                continue
            if known_ids is not None and txn.transaction_id in seen:
                continue
            collected[txn.transaction_id] = txn
        if on_event and not quiet:
            on_event(
                {
                    "kind": "log",
                    "message": (
                        f"{tx_type} HTTP page {page_num}/{page_limit} · "
                        f"{len(collected)} unique of {total_count or '?'} Completed records."
                    ),
                }
            )

    pages_read = 1
    _take(first, 1)
    if total_count and not collected and not seen:
        return None
    missing = 0 if expect_new is None else max(0, int(expect_new))
    need_more = catch_up or (known_ids is not None and missing > len(collected))
    if need_more:
        indexes = list(range(1, page_limit))
        if known_ids is not None and not catch_up and total_pages > 1:
            indexes = list(range(total_pages - 1, 0, -1))[: max(page_limit - 1, 0)]
        for page_index in indexes:
            if limit and len(collected) >= limit:
                break
            if known_ids is None and total_count and len(collected) >= total_count:
                break
            if known_ids is not None and missing and len(collected) >= missing:
                break
            payload = unwrap_list_payload(
                post(LIST_PATH, list_filter_payload(settings, page_index, tx_type))
            )
            if not payload:
                break
            before = len(collected)
            pages_read += 1
            _take(payload, page_index + 1)
            if len(collected) == before and catch_up:
                break

    rows = list(collected.values())
    if limit:
        rows = rows[:limit]
    return ApiCapture(
        transactions=rows,
        website_records=total_count,
        website_total=_website_total(first.get("totalAmount")),
        pages=pages_read,
    )


def fetch_completed(
    post: PostFn,
    settings: Settings,
    limit: int | None = None,
    on_event=None,
    max_pages: int | None = None,
    known_ids: set[str] | None = None,
    catch_up: bool = True,
    quiet: bool = False,
    expect_new: int | None = None,
) -> ApiCapture | None:
    types = staff_scrape_types(settings.filter_type)
    collected: dict[str, Transaction] = {}
    total_count = 0
    total_amount = 0.0
    pages_read = 0
    saw_payload = False
    remaining = None if expect_new is None else max(0, int(expect_new))
    for tx_type in types:
        capture = _fetch_completed_type(
            post,
            settings,
            tx_type,
            limit=limit,
            on_event=on_event,
            max_pages=max_pages,
            known_ids=known_ids,
            catch_up=catch_up,
            quiet=quiet,
            expect_new=remaining,
        )
        if capture is None:
            continue
        saw_payload = True
        total_count += int(capture.website_records or 0)
        total_amount += parse_amount(capture.website_total)
        pages_read += int(capture.pages or 0)
        for txn in capture.transactions:
            if txn.transaction_id and txn.transaction_id not in collected:
                collected[txn.transaction_id] = txn
        if remaining is not None:
            remaining = max(0, remaining - len(capture.transactions))
            if remaining == 0 and known_ids is not None and not catch_up:
                break
        if limit and len(collected) >= limit:
            break
    if not saw_payload:
        return None
    rows = list(collected.values())
    if limit:
        rows = rows[:limit]
    return ApiCapture(
        transactions=rows,
        website_records=total_count,
        website_total=format_amount(total_amount) if total_count or total_amount else "",
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


def post_with_page(page, path: str, data: dict[str, str], origin: str = "") -> tuple[Any, str]:
    """POST the list using the logged-in page cookies, without jQuery (no site spinner)."""
    token = ""
    try:
        token = str(page.evaluate(READ_ADMIN_TOKEN_JS) or "")
    except Exception:
        token = ""
    url = (origin or "").rstrip("/") + path
    raw = None
    error = ""
    try:
        response = page.request.post(
            url,
            form=data,
            headers=_request_headers(origin, token),
            timeout=8000,
        )
        if response.status >= 400:
            error = f"HTTP {response.status}"
        else:
            try:
                raw = response.json()
            except Exception:
                error = "response was not JSON"
    except Exception as exc:
        error = str(exc)
    payload = unwrap_list_payload(raw)
    if payload is not None:
        return payload, ""
    try:
        raw = page.evaluate(LIVE_POST_JS, data)
    except Exception as exc:
        return None, error or str(exc)
    payload = unwrap_list_payload(raw)
    if payload is not None:
        return payload, ""
    status = ""
    if isinstance(raw, dict):
        status = str(raw.get("error") or raw.get("status") or "")
    return None, error or status or "dashboard did not return a transaction list"


class DashboardClient:
    def __init__(
        self,
        session: ApiSession,
        timeout: float | tuple[float, float] = (5.0, 8.0),
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.last_error = ""
        self.http = requests.Session()
        host = (urlparse(session.origin).hostname or "").lower()
        for cookie in session.cookies:
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if not name:
                continue
            domain = str(cookie.get("domain") or "").lstrip(".").lower()
            if domain and host and domain not in host and host not in domain:
                continue
            try:
                self.http.cookies.set(
                    name,
                    value,
                    domain=domain or None,
                    path=str(cookie.get("path") or "/") or "/",
                )
            except Exception:
                self.http.cookies.set(name, value)
        self.http.headers.update(_request_headers(session.origin, session.token))

    @classmethod
    def from_settings(cls, settings: Settings) -> DashboardClient | None:
        session = load_api_session(settings)
        if not session:
            return None
        return cls(session)

    def post(self, path: str, data: dict[str, str]) -> Any:
        url = self.session.origin.rstrip("/") + path
        try:
            response = self.http.post(url, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            self.last_error = str(exc)
            return None
        if response.status_code >= 400:
            self.last_error = f"HTTP {response.status_code}"
            return None
        try:
            raw = response.json()
        except ValueError:
            self.last_error = "response was not JSON"
            return None
        payload = unwrap_list_payload(raw)
        if payload is None:
            keys = (
                ",".join(sorted(str(key) for key in raw.keys()))
                if isinstance(raw, dict)
                else type(raw).__name__
            )
            self.last_error = f"unexpected JSON keys: {keys or 'none'}"
            return None
        self.last_error = ""
        return payload


def http_post(session: ApiSession, path: str, data: dict[str, str], timeout: int = 30) -> Any:
    return DashboardClient(session, timeout=timeout).post(path, data)


def scrape_via_http(
    settings: Settings,
    limit: int | None = None,
    on_event=None,
    known_ids: set[str] | None = None,
    catch_up: bool = True,
    quiet: bool = False,
    client: DashboardClient | object | None = None,
    expect_new: int | None = None,
) -> ApiCapture | None:
    transport = client or DashboardClient.from_settings(settings)
    if not transport:
        return None
    return fetch_completed(
        transport.post,
        settings,
        limit=limit,
        on_event=on_event,
        max_pages=settings.max_pages,
        known_ids=known_ids,
        catch_up=catch_up,
        quiet=quiet,
        expect_new=expect_new,
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
            raw = response.json()
        except Exception:
            return None
        return unwrap_list_payload(raw)

    return fetch_completed(
        post,
        settings,
        limit=limit,
        on_event=on_event,
        max_pages=settings.max_pages,
    )
