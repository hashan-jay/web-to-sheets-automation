from __future__ import annotations

import re

from src.config import Settings, normalize_google_sheet_id
from src.models import Transaction
from src.tally import is_withdraw_type

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

MAX_SHEET_BRANDS = 30
_BRAND_SPLIT_RE = re.compile(r"[\n\r,;|]+")

# Company Owner dropdown on Copy of GROUP D (Sheet 3).
GROUP_D_GAMES = (
    "FUCKFUCK",
    "AUSCLUB",
    "MM29",
    "CUNTHAUS",
    "METH365",
    "SLOTAUD",
    "SLOTROT",
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


def sheet_tab_name(raw: object) -> str:
    """Google Sheet tab title for a date, e.g. 2026-08-29 -> '29'."""
    key = date_key(raw)
    if key:
        return str(int(key.split("-")[2]))
    text = str(raw or "").strip()
    if text.isdigit():
        return str(int(text))
    return day_from_datetime(text)


def txn_local_date(txn: Transaction) -> str:
    stamped = date_key((txn.extras or {}).get("tally_date"))
    if stamped:
        return stamped
    return date_key(record_local_datetime(txn.processed, txn.datetime, txn.created))


def normalize_status(value: str) -> str:
    key = (value or "").strip().upper()
    return STATUS_MAP.get(key, value.title() if value else "")


_SKIP_BRAND_TOKENS = {
    "COPY",
    "DEPOSIT",
    "WITHDRAW",
    "WITHDRAWAL",
    "STAFF",
    "STAFFDEPOSIT",
    "STAFFWITHDRAW",
    "UNCLAIM",
    "MANUAL",
    "CREATED",
    "PROCESSED",
    "USERNAME",
    "NAME",
    "MOBILE",
    "AMOUNT",
    "BANK",
    "METHOD",
    "DATETIME",
    "GATEWAY",
    "PAYID",
    "BANKLOCK",
    "BANKBSB",
}


def first_brand_tag(*parts: str) -> str:
    """Return the first dashboard brand badge, ignoring NETLOSS labels.

    On the admin site the first black/blue pill after the player name is the
    brand (e.g. FUCKFUCKVIPC). The second pill is a loss tag such as NETLOSSB.
    """
    for part in parts:
        for token in re.split(r"[\s,|/]+", part or ""):
            tag = token.strip("[]() ")
            if len(tag) < 3 or len(tag) > 40:
                continue
            upper = tag.upper()
            if upper.startswith("NETLOSS") or upper in _SKIP_BRAND_TOKENS:
                continue
            if not any(ch.isalpha() for ch in tag):
                continue
            if re.fullmatch(r"[A-Za-z0-9._-]+", tag):
                return upper
    return ""


def resolve_brand(*parts: str, default: str = "") -> str:
    """Return the first brand badge from the given tags, else default."""
    return first_brand_tag(*parts) or default


def captured_brand(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    return first_brand_tag(raw) or (
        raw.upper() if not raw.upper().startswith("NETLOSS") else ""
    )


def brand_compact(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def brand_stem(value: object) -> str:
    return re.sub(r"\d+$", "", brand_compact(value))


def normalize_sheet_brands(raw: object) -> list[str]:
    """Unique website brand names for the Google Sheet, at most 30."""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = _BRAND_SPLIT_RE.split(raw)
    elif isinstance(raw, (list, tuple, set)):
        parts = []
        for item in raw:
            parts.extend(_BRAND_SPLIT_RE.split(str(item or "")))
    else:
        parts = [str(raw)]
    seen: set[str] = set()
    brands: list[str] = []
    for part in parts:
        name = str(part or "").strip()
        key = brand_compact(name)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        brands.append(name)
        if len(brands) >= MAX_SHEET_BRANDS:
            break
    return brands


def sheet_brand_choices(settings: Settings | None) -> tuple[str, ...]:
    if settings is None:
        return ()
    return tuple(normalize_sheet_brands(getattr(settings, "sheet_brands", ())))


def match_site_brand(scraped: object, brands: tuple[str, ...] | list[str]) -> str:
    """Map a dashboard badge onto a configured sheet brand name.

    KABOOM77VIPA / KABOOMVIPA / VIPA → KABOOM77 when that name is configured.
    Several configured names use the longest unique prefix or letter-stem.
    """
    names = normalize_sheet_brands(brands)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    captured = captured_brand(str(scraped or "")) or str(scraped or "").strip()
    compact = brand_compact(captured)
    if not compact:
        return ""
    for name in names:
        if brand_compact(name) == compact:
            return name
    ranked = sorted(names, key=lambda name: len(brand_compact(name)), reverse=True)
    for name in ranked:
        key = brand_compact(name)
        if len(key) >= 3 and compact.startswith(key):
            return name
    for name in ranked:
        stem = brand_stem(name)
        if len(stem) >= 4 and compact.startswith(stem):
            return name
    for name in ranked:
        key = brand_compact(name)
        if len(key) >= 4 and key in compact:
            return name
    return ""


def normalize_brand(value: str, settings: Settings | None = None) -> str:
    brands = sheet_brand_choices(settings)
    if brands:
        return match_site_brand(value, brands)
    return captured_brand(value)


def uses_group_d_games(spreadsheet_title: str) -> bool:
    title = " ".join(
        (spreadsheet_title or "").strip().lower().replace("-", " ").replace("_", " ").split()
    )
    return "group d" in title


def sheet_game_choices(
    settings: Settings | None,
    sheet_id: str = "",
    spreadsheet_title: str = "",
) -> tuple[str, ...] | None:
    if uses_group_d_games(spreadsheet_title):
        return GROUP_D_GAMES
    third = ""
    if settings is not None:
        third = normalize_google_sheet_id(getattr(settings, "google_sheet_id_3", ""))
    current = normalize_google_sheet_id(sheet_id)
    if third and current and third == current:
        return GROUP_D_GAMES
    return None


def match_sheet_game(brand: str, games: tuple[str, ...] = GROUP_D_GAMES) -> str:
    """Map a website brand badge onto a GROUP D game dropdown value."""
    captured = captured_brand(brand)
    if not captured:
        return ""
    upper = captured.upper()
    ranked = sorted((game.upper() for game in games if game), key=len, reverse=True)
    for game in ranked:
        if upper == game:
            return game
    for game in ranked:
        if upper.startswith(game) or game.startswith(upper):
            return game
    for game in ranked:
        if game in upper or upper in game:
            return game
    return ""


def is_withdraw(status: object) -> bool:
    return is_withdraw_type(status)


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
    """Company BANK dropdown value for deposits only. Withdrawals stay blank."""
    if is_withdraw(txn.status):
        return ""
    extras = txn.extras or {}
    return str(extras.get("sheet_bank") or "").strip()


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


DEFAULT_SHEET_COLUMNS = {
    "day": SHEET_COL_DAY,
    "date": SHEET_COL_DATE,
    "bank": SHEET_COL_BANK,
    "description": SHEET_COL_DESCRIPTION,
    "amount": SHEET_COL_AMOUNT,
    "status": SHEET_COL_STATUS,
    "id": SHEET_COL_ID,
    "company": SHEET_COL_COMPANY,
    "company_trf": SHEET_COL_COMPANY_TRF,
    "player": SHEET_COL_PLAYER,
    "unused": SHEET_COL_UNUSED,
    "staff": SHEET_COL_STAFF,
}

# Header labels on the Google Sheet → transaction field written into that column.
HEADER_ALIASES = {
    "id": ("id", "transaction id", "txn id", "id transaction"),
    "day": ("day",),
    "date": ("date", "datetime", "date time", "time"),
    "bank": ("bank", "bank name"),
    "description": ("description", "desc", "acc name", "account name"),
    "name": ("name",),
    "amount": ("amount", "amt"),
    "status": ("status", "type"),
    "company": ("company", "company owner", "company name", "game"),
    "brand": ("brand",),
    "company_trf": ("company trf", "company transfer"),
    "player": ("player", "username", "user"),
    "staff": ("staff", "staff code"),
    "mobile": ("mobile", "phone"),
    "acc_no": ("acc no", "account no", "account number", "acc number"),
    "bsb": ("bsb",),
    "pay_id": ("payid", "pay id"),
    "method": ("method",),
    "created": ("created",),
    "processed": ("processed",),
    "bank_lock": ("banklock", "bank lock"),
}

CASHBOOK_FIELDS = (
    "day",
    "date",
    "bank",
    "description",
    "amount",
    "status",
    "id",
    "company",
    "player",
)


def empty_sheet_row() -> list[str]:
    return [""] * SHEET_COL_COUNT


def pad_sheet_row(row: list[str], width: int = 0) -> list[str]:
    size = max(SHEET_COL_COUNT, int(width or 0), len(row))
    padded = list(row) + [""] * (size - len(row))
    return padded[:size]


def normalize_header(value: object) -> str:
    return " ".join(
        str(value or "").strip().lower().replace("_", " ").replace("-", " ").split()
    )


def normalize_sheet_id(value: object) -> str:
    """Transaction ID as written on the sheet, including numeric scientific notation."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text
    compact = text.replace(",", "").replace(" ", "")
    if compact.isdigit():
        return compact
    try:
        number = float(compact)
    except ValueError:
        return ""
    if number.is_integer() and number > 0:
        as_int = str(int(number))
        if len(as_int) >= 6:
            return as_int
    return ""


def looks_like_sheet_headers(headers: list[object] | tuple[object, ...] | None) -> bool:
    wanted = {"id", "date", "day", "amount", "description", "status", "player"}
    found = {normalize_header(item) for item in (headers or [])}
    return bool(found & wanted)


def detect_sheet_columns(headers: list[object] | tuple[object, ...] | None) -> dict[str, int]:
    """Map Google Sheet header labels onto transaction fields.

    Unknown extra columns stay unused. Missing cashbook fields keep the
    default A–L positions when those cells are not already taken.
    """
    found: dict[str, int] = {}
    for index, raw in enumerate(headers or []):
        key = normalize_header(raw)
        if not key:
            continue
        for field, aliases in HEADER_ALIASES.items():
            if key in aliases and field not in found:
                found[field] = index
                break
    if "company" not in found and "brand" in found:
        found["company"] = found["brand"]
    if "description" not in found and "name" in found:
        found["description"] = found["name"]
    if not looks_like_sheet_headers(headers):
        return dict(DEFAULT_SHEET_COLUMNS)
    used = set(found.values())
    for field, column in DEFAULT_SHEET_COLUMNS.items():
        if field in found or column in used:
            continue
        found[field] = column
        used.add(column)
    return found


def sheet_company_value(
    txn: Transaction,
    settings: Settings,
    games: tuple[str, ...] | None = None,
) -> str:
    brands = sheet_brand_choices(settings)
    if brands:
        return match_site_brand(txn.brand, brands)
    if games:
        return match_sheet_game(txn.brand, games)
    return normalize_brand(txn.brand, settings)


def sheet_field_values(
    txn: Transaction,
    settings: Settings,
    games: tuple[str, ...] | None = None,
) -> dict[str, str]:
    when = record_local_datetime(txn.datetime, txn.created, txn.processed)
    company = sheet_company_value(txn, settings, games)
    username = (txn.username or "").strip()
    return {
        "day": day_from_datetime(when),
        "date": when,
        "bank": sheet_bank(txn, settings),
        "description": sheet_description(txn),
        "name": clean_name(txn.bank_account_name or txn.name),
        "amount": sheet_amount(txn.amount, txn.status),
        "status": sheet_status(txn.status),
        "id": txn.transaction_id,
        "company": company,
        "brand": company,
        "company_trf": "",
        "player": username,
        "username": username,
        "staff": "",
        "unused": "",
        "mobile": (txn.mobile or "").strip(),
        "acc_no": (txn.bank_account_number or "").strip(),
        "bsb": (txn.bsb or "").strip(),
        "pay_id": (txn.pay_id or "").strip(),
        "method": (txn.method or "").strip(),
        "created": record_local_datetime(txn.created),
        "processed": record_local_datetime(txn.processed),
        "bank_lock": (txn.bank_lock or "").strip(),
    }


def to_sheet_row(
    txn: Transaction,
    settings: Settings,
    games: tuple[str, ...] | None = None,
    columns: dict[str, int] | None = None,
) -> list[str]:
    """Write one transaction onto the Google Sheet columns.

    Default cashbook:
    A DAY | B DATE | C BANK | D DESCRIPTION | E AMOUNT | F STATUS | G ID |
    H COMPANY OWNER / COMPANY NAME | I COMPANY TRF | J PLAYER | K *(blank)* | L STAFF

    When `columns` comes from the live tab headers, extra fields such as
    mobile, BSB, and PayID are written only into those matching headers.
    Existing rows are never updated by this function.
    """
    fields = sheet_field_values(txn, settings, games)
    cols = dict(columns or DEFAULT_SHEET_COLUMNS)
    width = max(SHEET_COL_COUNT, max(cols.values(), default=SHEET_COL_COUNT) + 1)
    row = [""] * width
    for field, column in cols.items():
        if field not in fields:
            continue
        if column < 0:
            continue
        if column >= len(row):
            row.extend([""] * (column + 1 - len(row)))
        row[column] = fields[field]
    return row
