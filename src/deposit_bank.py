from __future__ import annotations

import html as html_lib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse

from src.config import Settings
from src.mapper import is_withdraw
from src.models import Transaction

EventFn = Callable[..., None]

_URL_RE = re.compile(
    r"""(?P<url>https?://[^\s"'<>\\]+|/[^\s"'<>\\]+\.(?:png|jpe?g|webp|gif|bmp|pdf))""",
    re.I,
)
_IMG_SRC_RE = re.compile(
    r"""<img[^>]+src=["']([^"']+)["']""",
    re.I,
)
_ATTACHMENT_KEYS = (
    "attachment",
    "attachments",
    "receipt",
    "receipturl",
    "receiptimage",
    "receiptimg",
    "image",
    "imageurl",
    "img",
    "file",
    "fileurl",
    "filename",
    "proof",
    "depositproof",
    "screenshot",
    "upload",
    "uploads",
    "document",
    "documents",
    "slip",
    "payslip",
)
_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/bmp",
}


def _norm(value: object) -> str:
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).upper()
    return " ".join(text.split())


def _tokens(value: object) -> list[str]:
    return [part for part in _norm(value).split() if len(part) > 1]


def sheet_bank_choices(settings: Settings | None) -> tuple[str, ...]:
    items: list[str] = []
    if settings is not None:
        items.extend(str(item).strip() for item in (settings.bank_accounts or ()))
        default = str(getattr(settings, "default_bank_account", "") or "").strip()
        if default:
            items.insert(0, default)
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        key = _norm(item)
        if not item or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


_GENERIC_BANK_TOKENS = {
    "THE",
    "AND",
    "OF",
    "BANK",
    "LIMITED",
    "AUSTRALIA",
    "NATIONAL",
    "FIRST",
    "PTY",
    "LTD",
    "PLUS",
    "MANUAL",
    "GATEWAY",
}


def _distinctive_tokens(value: object) -> list[str]:
    return [
        part
        for part in _tokens(value)
        if len(part) > 2 and part not in _GENERIC_BANK_TOKENS
    ]


def match_sheet_bank(text: object, choices: tuple[str, ...] | list[str]) -> str:
    """Return the dropdown label whose parts all appear in the screenshot text."""
    hay = _norm(text)
    if not hay or not choices:
        return ""
    ranked = sorted((str(item).strip() for item in choices if str(item).strip()), key=len, reverse=True)
    for choice in ranked:
        parts = [part for part in _tokens(choice) if part not in {"THE", "AND", "OF"}]
        if parts and all(part in hay for part in parts):
            return choice
    best = ""
    best_score = 0
    for choice in ranked:
        distinctive = _distinctive_tokens(choice)
        if not distinctive:
            continue
        score = sum(1 for part in distinctive if part in hay)
        need = 2 if len(distinctive) >= 2 else 1
        name_hits = [part for part in distinctive if len(part) >= 4 and part in hay]
        if score >= need and name_hits and score > best_score:
            best = choice
            best_score = score
    if best:
        return best
    for choice in ranked:
        parts = [part for part in _tokens(choice) if len(part) > 2]
        if not parts:
            continue
        score = sum(1 for part in parts if part in hay)
        name_hits = [
            part
            for part in parts
            if len(part) > 3 and part not in _GENERIC_BANK_TOKENS and part in hay
        ]
        if score >= max(2, (len(parts) + 1) // 2) and name_hits and score > best_score:
            best = choice
            best_score = score
    return best


def extract_attachment_url(raw: object, origin: str = "") -> str:
    """Find a receipt/screenshot URL in API JSON or a stored attachment field."""
    found = _first_url(raw)
    return _abs_url(found, origin)


def _first_url(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() in {"none", "null", "undefined", "attachment"}:
            return ""
        if text.startswith("{") or text.startswith("["):
            try:
                import json

                return _first_url(json.loads(text))
            except Exception:
                pass
        match = _URL_RE.search(text)
        if match:
            return match.group("url")
        if text.startswith("/") or "://" in text:
            return text
        return ""
    if isinstance(raw, dict):
        extras = raw.get("extras") if isinstance(raw.get("extras"), dict) else {}
        for key in ("attachment", "sheet_attachment"):
            if extras.get(key):
                found = _first_url(extras.get(key))
                if found:
                    return found
        for key, value in raw.items():
            if str(key).strip().lower() in _ATTACHMENT_KEYS:
                found = _first_url(value)
                if found:
                    return found
        for nest in ("details", "data", "receipt", "file", "image"):
            if nest in raw:
                found = _first_url(raw.get(nest))
                if found:
                    return found
        return ""
    if isinstance(raw, (list, tuple)):
        for item in raw:
            found = _first_url(item)
            if found:
                return found
    return ""


def _abs_url(url: str, origin: str) -> str:
    text = str(url or "").strip().strip("'\"")
    if not text or text.lower().startswith("javascript:"):
        return ""
    if text.startswith("//"):
        return "https:" + text
    if origin and text.startswith("/"):
        return urljoin(origin.rstrip("/") + "/", text.lstrip("/"))
    return text


def txn_attachment_url(txn: Transaction, origin: str = "") -> str:
    extras = txn.extras or {}
    return extract_attachment_url(
        extras.get("attachment")
        or extras.get("sheet_attachment")
        or getattr(txn, "attachment", "")
        or extras.get("details")
        or "",
        origin,
    )


def _emit(on_event: EventFn | None, **payload: object) -> None:
    if on_event:
        on_event(payload)


def normalize_deposit_sheet_bank(raw: object) -> str:
    """Exact Google Sheet BANK dropdown label, or blank if the user left it empty."""
    return " ".join(str(raw or "").split())


def apply_deposit_sheet_bank(
    settings: Settings | None,
    transactions: list[Transaction],
) -> int:
    """Stamp the live GUI deposit bank onto deposit rows only.

    Withdrawals stay untouched. An empty box writes a blank Bank cell.
    """
    bank = normalize_deposit_sheet_bank(
        getattr(settings, "deposit_sheet_bank", "") if settings is not None else ""
    )
    filled = 0
    for txn in transactions:
        if is_withdraw(txn.status):
            continue
        extras = dict(txn.extras or {})
        extras["sheet_bank"] = bank
        txn.extras = extras
        if bank:
            filled += 1
    return filled


def fill_deposit_banks(
    settings: Settings,
    transactions: list[Transaction],
    on_event: EventFn | None = None,
    choices: tuple[str, ...] | None = None,
    http=None,
    origin: str = "",
) -> int:
    """Use the live GUI deposit bank. Attachment screenshot OCR stays off."""
    filled = apply_deposit_sheet_bank(settings, transactions)
    if filled and on_event:
        bank = normalize_deposit_sheet_bank(getattr(settings, "deposit_sheet_bank", ""))
        _emit(
            on_event,
            kind="log",
            message=f"Deposit BANK for this write: {bank}.",
        )
    return filled


def resolve_deposit_bank(
    txn: Transaction,
    choices: tuple[str, ...],
    session=None,
    origin: str = "",
    cache: dict[str, str] | None = None,
) -> str:
    if is_withdraw(txn.status):
        return ""
    extras = txn.extras or {}
    already = str(extras.get("sheet_bank") or "").strip()
    if already:
        return already
    url = lookup_attachment_url(txn, session, origin)
    if not url:
        return ""
    store = cache if cache is not None else {}
    if url in store:
        return store[url]
    text = read_attachment_text(url, session)
    bank = match_sheet_bank(text, choices)
    store[url] = bank
    return bank


_DETAIL_PATHS = (
    "/transactions/getTransaction",
    "/transactions/getTransactionById",
    "/transactions/get",
    "/transactions/detail",
    "/transaction/get",
)


def lookup_attachment_url(txn: Transaction, session=None, origin: str = "") -> str:
    url = txn_attachment_url(txn, origin)
    if url:
        return url
    host = (origin or "").rstrip("/")
    txn_id = str(txn.transaction_id or "").strip()
    if session is None or not host or not txn_id:
        return ""
    payload = {"id": txn_id, "transactionId": txn_id}
    for path in _DETAIL_PATHS:
        try:
            response = session.post(host + path, data=payload, timeout=8)
            raw = response.json()
        except Exception:
            continue
        found = extract_attachment_url(raw, host)
        if found:
            extras = dict(txn.extras or {})
            extras["attachment"] = found
            txn.extras = extras
            txn.attachment = found
            return found
    return ""


def read_attachment_text(url: str, session=None) -> str:
    body, content_type, final_url = _download(url, session)
    if not body:
        return ""
    if "html" in content_type or body.lstrip()[:32].lower().startswith((b"<!doctype", b"<html")):
        page = body.decode("utf-8", errors="ignore")
        text = _visible_html_text(page)
        for src in _IMG_SRC_RE.findall(page):
            image_url = _abs_url(html_lib.unescape(src), final_url or url)
            if not image_url:
                continue
            image_body, image_type, _final = _download(image_url, session)
            if image_body and (
                image_type in _IMAGE_TYPES or _looks_like_image(image_body)
            ):
                text = f"{text} {ocr_image_bytes(image_body)}".strip()
        return text
    if content_type in _IMAGE_TYPES or _looks_like_image(body):
        return ocr_image_bytes(body)
    return body.decode("utf-8", errors="ignore")


def _visible_html_text(page: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", page)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    return html_lib.unescape(re.sub(r"\s+", " ", cleaned)).strip()


def _looks_like_image(body: bytes) -> bool:
    return body[:8].startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"RIFF"))


def _download(url: str, session=None) -> tuple[bytes, str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return b"", "", url
    try:
        if session is not None:
            response = session.get(url, timeout=20, allow_redirects=True)
        else:
            import requests

            response = requests.get(url, timeout=20, allow_redirects=True)
    except Exception:
        return b"", "", url
    if getattr(response, "status_code", 0) >= 400:
        return b"", "", url
    content_type = str(getattr(response, "headers", {}).get("Content-Type") or "").split(";")[0].strip().lower()
    return response.content or b"", content_type, str(getattr(response, "url", url) or url)


def ocr_image_bytes(body: bytes) -> str:
    if not body:
        return ""
    for reader in (_ocr_pytesseract, _ocr_windows):
        try:
            text = reader(body)
        except Exception:
            text = ""
        if text.strip():
            return text
    return ""


def _ocr_pytesseract(body: bytes) -> str:
    import io

    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(body))
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    return str(pytesseract.image_to_string(image) or "")


def _ocr_windows(body: bytes) -> str:
    if not shutil.which("powershell"):
        return ""
    suffix = ".png"
    if body[:3] == b"\xff\xd8\xff":
        suffix = ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(body)
        path = handle.name
    script = (
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null; "
        "$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]; "
        "$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]; "
        "$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]; "
        "$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | "
        "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 } | "
        "Select-Object -First 1; "
        "function Await($WinRtTask, $ResultType) { "
        "$netTask = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask)); "
        "$netTask.Wait(-1) | Out-Null; $netTask.Result }; "
        f"$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{path}')) "
        "([Windows.Storage.StorageFile]); "
        "$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) "
        "([Windows.Storage.Streams.IRandomAccessStream]); "
        "$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) "
        "([Windows.Graphics.Imaging.BitmapDecoder]); "
        "$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) "
        "([Windows.Graphics.Imaging.SoftwareBitmap]); "
        "$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages(); "
        "if (-not $engine) { return }; "
        "$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]); "
        "$result.Text"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return (completed.stdout or "").strip()
    finally:
        Path(path).unlink(missing_ok=True)


_BANK_HEADER = {"BANK", "BANK ACCOUNT", "DAY", "ACCOUNT", "ACCOUNT NAME"}


def discover_bank_choices(sheet) -> tuple[str, ...]:
    """Learn BANK dropdown labels from the open spreadsheet when possible."""
    cached = getattr(sheet, "_bank_choices", None)
    if cached is not None:
        return cached
    found: list[str] = []
    spreadsheet = getattr(sheet, "spreadsheet", None)
    ws = getattr(sheet, "ws", None)
    try:
        if ws is not None:
            found.extend(_clean_bank_labels(ws.col_values(3)[:400]))
            found.extend(_validation_bank_labels(spreadsheet, ws))
    except Exception:
        pass
    try:
        worksheets = None
        getter = getattr(sheet, "_cached_worksheets", None)
        if callable(getter):
            worksheets = getter()
        elif spreadsheet is not None:
            worksheets = spreadsheet.worksheets()
        if worksheets:
            for worksheet in worksheets:
                title = str(worksheet.title or "").strip().lower()
                if title in {"banks", "bank", "dropdown", "dropdowns", "bank accounts"}:
                    for row in worksheet.get_all_values()[:400]:
                        found.extend(_clean_bank_labels(row))
    except Exception:
        pass
    unique = _unique_bank_labels(found)
    try:
        sheet._bank_choices = unique
    except Exception:
        pass
    return unique


def _unique_bank_labels(found: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in found:
        key = _norm(item)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def _clean_bank_labels(values: list | tuple) -> list[str]:
    labels: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text.upper() not in _BANK_HEADER:
            labels.append(text)
    return labels


def _validation_bank_labels(spreadsheet, worksheet) -> list[str]:
    if spreadsheet is None or worksheet is None:
        return []
    title = str(getattr(worksheet, "title", "") or "").strip()
    if not title:
        return []
    try:
        meta = spreadsheet.fetch_sheet_metadata(
            {
                "includeGridData": True,
                "ranges": [f"'{title}'!C105:C105"],
            }
        )
    except Exception:
        return []
    for block in (meta.get("sheets") or []) if isinstance(meta, dict) else []:
        for data in block.get("data") or []:
            for row in data.get("rowData") or []:
                for cell in row.get("values") or []:
                    condition = ((cell.get("dataValidation") or {}).get("condition") or {})
                    kind = str(condition.get("type") or "")
                    values = condition.get("values") or []
                    if kind == "ONE_OF_LIST":
                        return _clean_bank_labels(
                            item.get("userEnteredValue")
                            for item in values
                            if isinstance(item, dict)
                        )
                    if kind == "ONE_OF_RANGE" and values:
                        formula = str(values[0].get("userEnteredValue") or "")
                        return _labels_from_range_formula(spreadsheet, formula)
    return []


def _labels_from_range_formula(spreadsheet, formula: str) -> list[str]:
    text = str(formula or "").strip().lstrip("=")
    match = re.match(
        r"'?([^'!]+)'?!\$?([A-Za-z]+)\$?(\d+):\$?([A-Za-z]+)\$?(\d+)",
        text,
    )
    if not match:
        return []
    title, col1, row1, col2, row2 = match.groups()
    a1 = f"'{title}'!{col1}{row1}:{col2}{row2}"
    try:
        raw = spreadsheet.values_get(a1)
        rows = raw.get("values") if isinstance(raw, dict) else raw
    except Exception:
        return []
    found: list[str] = []
    for row in rows or []:
        found.extend(_clean_bank_labels(row if isinstance(row, (list, tuple)) else [row]))
    return found
