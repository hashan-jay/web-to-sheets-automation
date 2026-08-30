from __future__ import annotations

from dataclasses import dataclass

from src.config import ROOT
from src.models import Transaction
from src.parser import parse_transactions_from_text


@dataclass
class OpenDashboard:
    url: str
    title: str
    text: str


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def read_open_dashboard() -> OpenDashboard | None:
    try:
        import uiautomation as auto
    except ImportError:
        return None

    auto.SetGlobalSearchTimeout(2.0)
    best: OpenDashboard | None = None
    for window in auto.GetRootControl().GetChildren():
        title = window.Name or ""
        if not any(name in title for name in ("Chrome", "Edge", "Brave")):
            continue
        url = ""
        try:
            edit = window.EditControl(Name="Address and search bar")
            if edit.Exists(0.2, 0):
                url = _normalize_url(edit.GetValuePattern().Value)
        except Exception:
            url = ""

        text = ""
        try:
            stack = list(window.GetChildren())
            seen = 0
            while stack and seen < 200:
                node = stack.pop(0)
                seen += 1
                name = node.Name or ""
                if "DevTools" in name:
                    continue
                try:
                    if getattr(node, "ControlTypeName", "") == "DocumentControl":
                        pattern = node.GetTextPattern()
                        if pattern:
                            candidate = pattern.DocumentRange.GetText(-1) or ""
                            if "Username:" in candidate and len(candidate) > len(text):
                                text = candidate
                except Exception:
                    pass
                try:
                    stack.extend(node.GetChildren())
                except Exception:
                    pass
        except Exception:
            text = ""

        looks_like_admin = (
            "Username:" in text
            or "Admin" in title
            or "as6868" in url
            or "transaction" in url.lower()
        )
        if not looks_like_admin:
            continue
        if url.endswith("#login"):
            url = url[:-6] + "#transactions"
        candidate = OpenDashboard(url=url, title=title, text=text)
        if best is None or len(text) > len(best.text):
            best = candidate
    return best


def scrape_open_browser() -> tuple[list[Transaction], OpenDashboard | None]:
    page = read_open_dashboard()
    if not page or not page.text:
        return [], page
    return parse_transactions_from_text(page.text), page


def persist_dashboard_url(url: str) -> None:
    if not url:
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(f"DASHBOARD_URL={url}\n", encoding="utf-8")
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for index, line in enumerate(lines):
        if line.startswith("DASHBOARD_URL="):
            lines[index] = f"DASHBOARD_URL={url}"
            updated = True
            break
    if not updated:
        lines.append(f"DASHBOARD_URL={url}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
