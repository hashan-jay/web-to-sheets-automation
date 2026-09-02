from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from src.errors import ConfigError

ROOT = Path(__file__).resolve().parent.parent
LOGIN_ACCOUNT_SLOTS = (1, 2, 3)
_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def persist_env_values(updates: dict[str, str]) -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(
            "".join(f"{key}={value}\n" for key, value in updates.items()),
            encoding="utf-8",
        )
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        for key, value in updates.items():
            if line.startswith(f"{key}="):
                lines[index] = f"{key}={value}"
                seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def login_slot(raw: object) -> int:
    try:
        slot = int(str(raw or "1").strip())
    except ValueError:
        return 1
    return slot if slot in LOGIN_ACCOUNT_SLOTS else 1


def login_account_keys(slot: int) -> dict[str, str]:
    number = login_slot(slot)
    return {
        "website": f"LOGIN_{number}_URL",
        "username": f"LOGIN_{number}_USERNAME",
        "password": f"LOGIN_{number}_PASSWORD",
        "twofa": f"LOGIN_{number}_2FA",
    }


def normalize_google_sheet_id(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = _SHEET_URL_RE.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", text):
        return text
    return ""


def google_sheet_url(sheet_id: object) -> str:
    key = normalize_google_sheet_id(sheet_id)
    return f"https://docs.google.com/spreadsheets/d/{key}" if key else ""


def service_account_email(credentials_path: Path) -> str:
    try:
        data = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("client_email") or "").strip()


def normalize_dashboard_url(raw: object) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if "#login" in url:
        url = url.replace("#login", "#transactions")
    elif "#" not in url:
        url = url.rstrip("/") + "/#transactions"
    return url


def load_login_accounts() -> list[dict[str, str]]:
    _load_env()
    accounts: list[dict[str, str]] = []
    for slot in LOGIN_ACCOUNT_SLOTS:
        keys = login_account_keys(slot)
        accounts.append(
            {
                "slot": str(slot),
                "website": normalize_dashboard_url(os.getenv(keys["website"], "")),
                "username": os.getenv(keys["username"], "").strip(),
                "password": os.getenv(keys["password"], ""),
                "twofa": os.getenv(keys["twofa"], "").strip(),
            }
        )
    if not accounts[0]["username"] and not accounts[0]["website"]:
        accounts[0] = {
            "slot": "1",
            "website": normalize_dashboard_url(os.getenv("DASHBOARD_URL", "")),
            "username": os.getenv("DASHBOARD_USERNAME", "").strip(),
            "password": os.getenv("DASHBOARD_PASSWORD", ""),
            "twofa": os.getenv("DASHBOARD_2FA", "").strip(),
        }
    elif not accounts[0]["website"]:
        accounts[0]["website"] = normalize_dashboard_url(os.getenv("DASHBOARD_URL", ""))
    return accounts


def active_login_slot() -> int:
    _load_env()
    return login_slot(os.getenv("LOGIN_ACTIVE_SLOT", "1"))


def persist_login_account(
    slot: int, website: str, username: str, password: str, twofa: str
) -> None:
    number = login_slot(slot)
    keys = login_account_keys(number)
    url = normalize_dashboard_url(website)
    persist_env_values(
        {
            keys["website"]: url,
            keys["username"]: username,
            keys["password"]: password,
            keys["twofa"]: twofa,
            "LOGIN_ACTIVE_SLOT": str(number),
            "DASHBOARD_URL": url,
            "DASHBOARD_USERNAME": username,
            "DASHBOARD_PASSWORD": password,
            "DASHBOARD_2FA": twofa,
        }
    )


def _load_env() -> None:
    load_dotenv(ROOT / ".env")
    bundled = ROOT / "ms-playwright"
    if bundled.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))
        return
    if os.name == "nt":
        browsers = Path.home() / "AppData" / "Local" / "ms-playwright"
        if browsers.exists():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _path(name: str, default: str) -> Path:
    raw = os.getenv(name, default).strip()
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _aliases(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        source, dest = part.split(":", 1)
        mapping[source.strip().upper()] = dest.strip()
    return mapping


@dataclass
class Settings:
    dashboard_url: str
    dashboard_username: str
    dashboard_password: str
    dashboard_2fa: str
    manual_login_seconds: int
    filter_date_from: str
    filter_date_to: str
    filter_type: str
    filter_status: str
    google_sheet_id: str
    google_worksheet: str
    google_credentials_path: Path
    default_bank_account: str
    default_brand: str
    default_staff_code: str
    brand_aliases: dict[str, str] = field(default_factory=dict)
    allowed_brands: tuple[str, ...] = (
        "POKIESPARK",
        "FUCKSPIN",
        "JOINTMATE",
        "HITMATE88",
        "AUZBETS",
        "WEMETH",
    )
    headed: bool = True
    slow_mo_ms: int = 0
    max_pages: int = 200
    poll_interval_seconds: int = 60
    use_open_browser: bool = True
    auth_state_path: Path = ROOT / "auth_state.json"
    database_path: Path = ROOT / "data" / "gathering.db"
    google_sheet_id_2: str = ""

    def sheet_ids(self) -> list[str]:
        ids: list[str] = []
        for raw in (self.google_sheet_id, self.google_sheet_id_2):
            key = normalize_google_sheet_id(raw)
            if key and key not in ids:
                ids.append(key)
        return ids

    @classmethod
    def load(cls) -> Settings:
        _load_env()
        return cls(
            dashboard_url=os.getenv("DASHBOARD_URL", "").strip(),
            dashboard_username=os.getenv("DASHBOARD_USERNAME", "").strip(),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", "").strip(),
            dashboard_2fa=os.getenv("DASHBOARD_2FA", "").strip(),
            manual_login_seconds=_int("MANUAL_LOGIN_SECONDS", 0),
            filter_date_from=os.getenv("FILTER_DATE_FROM", "").strip(),
            filter_date_to=os.getenv("FILTER_DATE_TO", "").strip(),
            filter_type=os.getenv("FILTER_TYPE", "ACTIVE").strip(),
            filter_status=os.getenv("FILTER_STATUS", "COMPLETED").strip(),
            google_sheet_id=normalize_google_sheet_id(os.getenv("GOOGLE_SHEET_ID", "")),
            google_worksheet=os.getenv("GOOGLE_WORKSHEET", "").strip(),
            google_sheet_id_2=normalize_google_sheet_id(os.getenv("GOOGLE_SHEET_ID_2", "")),
            google_credentials_path=_path(
                "GOOGLE_CREDENTIALS_PATH", "credentials/service-account.json"
            ),
            default_bank_account=os.getenv(
                "DEFAULT_BANK_ACCOUNT", "ANZPLUS O'NEILL R W"
            ).strip(),
            default_brand=os.getenv("DEFAULT_BRAND", "FUCKSPIN").strip(),
            default_staff_code=os.getenv("DEFAULT_STAFF_CODE", "SL0017").strip(),
            brand_aliases=_aliases(
                os.getenv("BRAND_ALIASES", "FUCKSPINVIPA:FUCKSPIN,FUCKSPINVIPC:FUCKSPIN")
            ),
            allowed_brands=tuple(
                part.strip().upper()
                for part in os.getenv(
                    "ALLOWED_BRANDS",
                    "POKIESPARK,FUCKSPIN,JOINTMATE,HITMATE88,AUZBETS,WEMETH",
                ).split(",")
                if part.strip()
            ),
            headed=_bool("HEADED", True),
            slow_mo_ms=_int("SLOW_MO_MS", 0),
            max_pages=_int("MAX_PAGES", 200),
            poll_interval_seconds=_int("POLL_INTERVAL_SECONDS", 60),
            use_open_browser=_bool("USE_OPEN_BROWSER", False),
        )

    def require_dashboard(self) -> None:
        if not self.dashboard_url and not self.use_open_browser:
            raise ConfigError(
                "No dashboard source. Open the admin site in Chrome or set DASHBOARD_URL."
            )

    def require_sheets(self) -> None:
        missing = []
        if not self.google_sheet_id:
            missing.append("GOOGLE_SHEET_ID")
        if not self.google_credentials_path.exists():
            missing.append(
                f"service account file at {self.google_credentials_path}"
            )
        if missing:
            raise ConfigError(
                "Google Sheets is not configured. Missing: " + ", ".join(missing)
            )
