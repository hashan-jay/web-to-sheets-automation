from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from src.config import (
    DEFAULT_DEPOSIT_START_ROW,
    DEFAULT_WITHDRAW_START_ROW,
    GOOGLE_SHEET_SLOTS,
    ROOT,
    Settings,
    normalize_dashboard_url,
    normalize_google_sheet_id,
    normalize_sheet_start_rows,
)
from src.mapper import normalize_sheet_brands

_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")
SITES_DIR = ROOT / "data" / "sites"
LEGACY_DATABASE = ROOT / "data" / "gathering.db"
LEGACY_AUTH = ROOT / "auth_state.json"
LEGACY_MARKER = SITES_DIR / ".legacy_migrated"
EMPTY_WORKSPACE = "_empty"


def website_host(website: str) -> str:
    url = normalize_dashboard_url(website)
    if not url:
        return ""
    host = (urlparse(url).hostname or "").strip().lower()
    if host:
        return host
    return url.split("://", 1)[-1].split("/", 1)[0].split("#", 1)[0].strip().lower()


def _safe_token(value: str) -> str:
    return _SAFE_RE.sub("_", (value or "").strip().lower()).strip("._-")


def workspace_key(website: str, username: str = "") -> str:
    host = _safe_token(website_host(website))
    user = _safe_token(username)
    if not host and not user:
        return ""
    if host and user:
        return f"{host}__{user}"
    return host or user


def workspace_dir(key: str) -> Path:
    name = key or EMPTY_WORKSPACE
    return SITES_DIR / name


def workspace_database_path(key: str) -> Path:
    return workspace_dir(key) / "gathering.db"


def workspace_state_path(key: str) -> Path:
    return workspace_dir(key) / "workspace.json"


def workspace_auth_path(key: str) -> Path:
    return workspace_dir(key) / "auth_state.json"


def empty_workspace_state() -> dict:
    return {
        "website": "",
        "username": "",
        "sheet_ids": [""] * len(GOOGLE_SHEET_SLOTS),
        "sheet_brands": [],
        "deposit_start_row": DEFAULT_DEPOSIT_START_ROW,
        "withdraw_start_row": DEFAULT_WITHDRAW_START_ROW,
    }


def load_workspace_state(key: str) -> dict:
    state = empty_workspace_state()
    if not key:
        return state
    path = workspace_state_path(key)
    if not path.exists():
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(data, dict):
        return state
    state["website"] = str(data.get("website") or "")
    state["username"] = str(data.get("username") or "")
    ids = list(data.get("sheet_ids") or [])
    padded = [""] * len(GOOGLE_SHEET_SLOTS)
    for index in range(len(GOOGLE_SHEET_SLOTS)):
        padded[index] = normalize_google_sheet_id(ids[index] if index < len(ids) else "")
    state["sheet_ids"] = padded
    state["sheet_brands"] = normalize_sheet_brands(data.get("sheet_brands"))
    deposit, withdraw = normalize_sheet_start_rows(
        data.get("deposit_start_row", DEFAULT_DEPOSIT_START_ROW),
        data.get("withdraw_start_row", DEFAULT_WITHDRAW_START_ROW),
    )
    state["deposit_start_row"] = deposit
    state["withdraw_start_row"] = withdraw
    return state


def save_workspace_state(
    key: str,
    *,
    website: str = "",
    username: str = "",
    sheet_ids: list[str] | None = None,
    sheet_brands: list[str] | None = None,
    deposit_start_row: int | None = None,
    withdraw_start_row: int | None = None,
) -> dict:
    if not key:
        return empty_workspace_state()
    current = load_workspace_state(key)
    if website:
        current["website"] = normalize_dashboard_url(website)
    if username or website:
        current["username"] = (username or "").strip()
    if sheet_ids is not None:
        padded = [""] * len(GOOGLE_SHEET_SLOTS)
        for index, value in enumerate(sheet_ids[: len(GOOGLE_SHEET_SLOTS)]):
            padded[index] = normalize_google_sheet_id(value)
        current["sheet_ids"] = padded
    if sheet_brands is not None:
        current["sheet_brands"] = normalize_sheet_brands(sheet_brands)
    if deposit_start_row is not None or withdraw_start_row is not None:
        deposit, withdraw = normalize_sheet_start_rows(
            current["deposit_start_row"] if deposit_start_row is None else deposit_start_row,
            current["withdraw_start_row"] if withdraw_start_row is None else withdraw_start_row,
        )
        current["deposit_start_row"] = deposit
        current["withdraw_start_row"] = withdraw
    path = workspace_state_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def seed_workspace_sheets(key: str, sheet_ids: list[str]) -> dict:
    state = load_workspace_state(key)
    if any(state["sheet_ids"]):
        return state
    if not any(normalize_google_sheet_id(item) for item in sheet_ids):
        return state
    return save_workspace_state(key, sheet_ids=sheet_ids)


def apply_workspace_to_settings(settings: Settings, key: str) -> Settings:
    settings.database_path = workspace_database_path(key)
    settings.auth_state_path = workspace_auth_path(key)
    state = load_workspace_state(key)
    settings.sheet_brands = tuple(state.get("sheet_brands") or ())
    deposit, withdraw = normalize_sheet_start_rows(
        state.get("deposit_start_row", settings.deposit_start_row),
        state.get("withdraw_start_row", settings.withdraw_start_row),
    )
    settings.deposit_start_row = deposit
    settings.withdraw_start_row = withdraw
    return settings


def migrate_legacy_workspace(key: str) -> bool:
    if not key:
        return False
    dest = workspace_database_path(key)
    if dest.exists() or LEGACY_MARKER.exists() or not LEGACY_DATABASE.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LEGACY_DATABASE, dest)
    auth_dest = workspace_auth_path(key)
    if LEGACY_AUTH.exists() and not auth_dest.exists():
        shutil.copy2(LEGACY_AUTH, auth_dest)
    LEGACY_MARKER.parent.mkdir(parents=True, exist_ok=True)
    LEGACY_MARKER.write_text(key, encoding="utf-8")
    return True
