from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from src.config import ROOT, Settings
from src.errors import ConfigError
from src.live_page import persist_dashboard_url, scrape_open_browser
from src.mapper import captured_brand
from src.models import Transaction
from src.tally import (
    COMPLETED_STATUS,
    estimated_pages,
    pager_finished,
    local_today,
    pager_bounds,
    pager_last_from_hrefs,
    parse_website_summary,
)

EXTRACT_SUMMARY_JS = r"""
() => {
  const text = document.body ? document.body.innerText : "";
  const record = text.match(/Record:\s*(-?\d+)/i);
  const total = text.match(/Total:\s*(-?[\d,.]+)/i);
  return {
    records: record ? Number(record[1]) : 0,
    total: total ? total[1] : "",
  };
}
"""

SET_STATUS_JS = r"""
(value) => {
  const wanted = String(value || "COMPLETED").toUpperCase();
  const selects = Array.from(document.querySelectorAll("select"));
  for (const sel of selects) {
    const label = ((sel.name || "") + " " + (sel.id || "") + " " +
      (sel.getAttribute("aria-label") || "") + " " +
      (sel.previousElementSibling && sel.previousElementSibling.innerText || "") + " " +
      (sel.parentElement && sel.parentElement.innerText || "")).toUpperCase();
    if (!label.includes("STATUS")) continue;
    const opt = Array.from(sel.options).find((item) =>
      String(item.text || "").toUpperCase().includes(wanted) ||
      String(item.value || "").toUpperCase().includes(wanted)
    );
    if (!opt) continue;
    sel.value = opt.value;
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return opt.value;
  }
  return "";
}
"""

PAGER_JS = r"""
() => {
  const root = document.querySelector(".pagination.simple-pagination, .simple-pagination");
  if (!root) return { current: 1, last: 0, labels: [], hrefs: [] };
  const active = root.querySelector("li.active span.current, li.active .current");
  const currentText = active ? String(active.textContent || "").trim() : "1";
  const current = Number(currentText) || 1;
  const hrefs = Array.from(root.querySelectorAll("a.page-link[href*='page-']"))
    .map((el) => el.getAttribute("href") || "");
  const fromHref = hrefs.map((href) => {
    const match = String(href).match(/page-(\d+)/i);
    return match ? Number(match[1]) : 0;
  }).filter(Boolean);
  const labels = Array.from(root.querySelectorAll("a.page-link, span.current"))
    .map((el) => String(el.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean);
  const last = fromHref.length ? Math.max(current, Math.max.apply(null, fromHref)) : current;
  return { current, last, labels, hrefs };
}
"""

CLICK_PAGER_JS = r"""
(args) => {
  const kind = args && args[0];
  const value = Number(args && args[1]) || 0;
  const root = document.querySelector(".pagination.simple-pagination, .simple-pagination");
  if (!root) return false;
  const jq = window.jQuery || window.$;
  if (kind === "page" && value && jq && jq.fn && jq.fn.pagination) {
    try {
      const pages = jq(root).pagination("getPagesCount");
      if (pages && value > Number(pages)) return false;
      jq(root).pagination("selectPage", value);
      return true;
    } catch (err) {}
  }
  let target = null;
  if (kind === "next") {
    target = root.querySelector("a.page-link.next, a.next.page-link");
    if (target && target.closest("li.disabled")) return false;
  } else if (value) {
    target = root.querySelector('a.page-link[href="#page-' + value + '"]');
  }
  if (!target) return false;
  target.scrollIntoView({ block: "center", inline: "nearest" });
  target.click();
  return true;
}
"""

PAGE_IDS_JS = r"""
() => Array.from(document.querySelectorAll(
  "#transactions-list tr[data-id], .list-wrapper tr[data-id], table tr[data-id]"
)).map((tr) => (tr.getAttribute("data-id") || "").trim()).filter(Boolean)
"""

SELECT_DATES_JS = r"""
(args) => {
  const from = String((args && args.from) || "").trim();
  const to = String((args && args.to) || from).trim();
  const ymd = (raw) => {
    const match = String(raw || "").match(/(\d{4}-\d{2}-\d{2})/);
    return match ? match[1] : "";
  };
  const compact = (raw) => String(raw || "").replace(/\s+/g, "");
  const isDoubled = (raw) => /^(\d{4}-\d{2}-\d{2})\1$/.test(compact(raw));
  const alreadySelected = (el, wanted) => {
    const raw = String((el && el.value) || "").trim();
    return Boolean(wanted) && ymd(raw) === wanted && !isDoubled(raw) && compact(raw).length <= 10;
  };
  const dateInputs = Array.from(document.querySelectorAll("input")).filter((el) => {
    const hay = ((el.name || "") + " " + (el.id || "") + " " +
      (el.placeholder || "") + " " + (el.className || "") + " " +
      (el.type || "")).toLowerCase();
    if (hay.includes("amount") || hay.includes("min") || hay.includes("max")) return false;
    return hay.includes("date") || el.type === "date";
  });
  const jq = window.jQuery || window.$;
  const parseWanted = (wanted) => {
    const parts = wanted.split("-").map(Number);
    if (parts.length !== 3 || parts.some((n) => !n)) return null;
    return { year: parts[0], month: parts[1], day: parts[2] };
  };
  const isToday = (wanted) => {
    const now = new Date();
    const parts = parseWanted(wanted);
    return Boolean(parts) &&
      now.getFullYear() === parts.year &&
      now.getMonth() + 1 === parts.month &&
      now.getDate() === parts.day;
  };
  const visiblePickers = () => Array.from(document.querySelectorAll(
    ".datepicker-dropdown, .datepicker.dropdown-menu, .datepicker, " +
    ".ui-datepicker, .flatpickr-calendar, .daterangepicker, .xdsoft_datetimepicker"
  )).filter((root) => {
    if (!root || root.offsetParent === null) return false;
    const style = window.getComputedStyle(root);
    return style.display !== "none" && style.visibility !== "hidden";
  });
  const clickTodayOrDay = (wanted) => {
    const parts = parseWanted(wanted);
    if (!parts) return false;
    const pickers = visiblePickers();
    for (const root of pickers) {
      if (isToday(wanted)) {
        const today = root.querySelector(
          "td.today:not(.old):not(.new), td.day.today, .ui-datepicker-today a, " +
          ".flatpickr-day.today, button.today, .datepicker-days td.today"
        );
        if (today) {
          today.click();
          return true;
        }
      }
      const cells = Array.from(root.querySelectorAll(
        "td.day:not(.old):not(.new), td[data-date], " +
        ".ui-datepicker-calendar td a, .flatpickr-day:not(.prevMonthDay):not(.nextMonthDay)"
      ));
      const cell = cells.find((td) => String(td.textContent || "").trim() === String(parts.day));
      if (cell) {
        cell.click();
        return true;
      }
    }
    return false;
  };
  const selectOne = (el, wanted) => {
    if (!el || !wanted) return "skip";
    if (alreadySelected(el, wanted)) return "already";
    if (jq && jq.fn) {
      const $el = jq(el);
      try {
        if ($el.data("datepicker") || $el.hasClass("hasDatepicker")) {
          const parts = parseWanted(wanted);
          if (parts) $el.datepicker("setDate", new Date(parts.year, parts.month - 1, parts.day));
          if (alreadySelected(el, wanted)) return "widget";
        }
      } catch (err) {}
      try {
        const drp = $el.data("daterangepicker");
        if (drp) {
          drp.setStartDate(wanted);
          drp.setEndDate(wanted);
          if (alreadySelected(el, wanted)) return "widget";
        }
      } catch (err) {}
    }
    el.focus();
    el.click();
    if (clickTodayOrDay(wanted)) return "picked";
    return "opened";
  };
  const first = dateInputs[0];
  const second = dateInputs[1];
  const results = [selectOne(first, from)];
  if (second && second !== first) results.push(selectOne(second, to));
  return { count: dateInputs.length, results };
}
"""

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

EXTRACT_CARDS_JS = r"""
() => {
  const aliases = {
    username: "Username",
    name: "Name",
    mobile: "Mobile",
    bankaccountname: "Bank Account Name",
    bankaccountnumber: "Bank Account Number",
    amount: "Amount",
    bank: "Bank",
    method: "Method",
    datetime: "Datetime",
    gateway: "Gateway",
    bankbsb: "BankBSB",
    payid: "PayID",
    banklock: "BankLock",
  };
  const stripTags = (value) => String(value || "").replace(/<[^>]+>/g, "").trim();
  const rows = document.querySelectorAll(
    "#transactions-list tr[data-id], .list-wrapper tr[data-id], table tr[data-id]"
  );
  return Array.from(rows).map((tr) => {
    const data = { transaction_id: (tr.getAttribute("data-id") || "").trim() };
    const type = tr.querySelector("div.type");
    if (type) data.status = type.textContent.trim().toUpperCase();
    const skipBrand = /^(COPY|NETLOSS|DEPOSIT|WITHDRAW|WITHDRAWAL|UNCLAIM|MANUAL|CREATED|PROCESSED)$/i;
    const isBrandPill = (value) => {
      const text = String(value || "").trim();
      if (text.length < 3 || text.length > 40) return false;
      if (skipBrand.test(text) || /^NETLOSS/i.test(text)) return false;
      return /^[A-Z0-9][A-Z0-9._-]*$/.test(text) && /[A-Z]/.test(text);
    };
    const pillSelectors = [
      "span.name-blacklist",
      "a.link.profile span",
      "a.profile span",
      "span.badge",
      "span.label",
      "span.tag",
    ];
    const pills = [];
    for (const selector of pillSelectors) {
      for (const el of tr.querySelectorAll(selector)) {
        const text = (el.textContent || "").trim();
        if (text && !pills.includes(text)) pills.push(text);
      }
    }
    if (!pills.length) {
      for (const el of tr.querySelectorAll("span")) {
        const cls = String(el.className || "");
        if (/\b(text|copy|hidden)\b/i.test(cls)) continue;
        const text = (el.textContent || "").trim();
        if (text && !pills.includes(text)) pills.push(text);
      }
    }
    data.brand = pills.find(isBrandPill) || "";
    for (const copy of tr.querySelectorAll("div.copy")) {
      const hidden = copy.querySelector("input.hidden, input[type='text']");
      let value = hidden && hidden.value ? stripTags(hidden.value) : "";
      const labelText = (copy.innerText || "").replace(/\bCOPY\b/g, "").trim();
      const idx = labelText.indexOf(":");
      if (idx === -1) continue;
      const key = labelText.slice(0, idx).replace(/\s+/g, "").toLowerCase();
      if (!value) value = labelText.slice(idx + 1).trim();
      const mapped = aliases[key];
      if (mapped && value) data[mapped] = value;
    }
    const action = tr.querySelectorAll("td")[1];
    const actionText = action ? action.innerText : "";
    const created = actionText.match(/CREATED\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/i);
    const processed = actionText.match(/PROCESSED\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})/i);
    if (created) data.created = created[1];
    if (processed) data.processed = processed[1];
    return data;
  }).filter((row) => row.transaction_id);
}
"""


def _fill_labeled_input(page: Page, label: str, value: str) -> None:
    if not value:
        return
    locator = page.get_by_label(re.compile(label, re.I))
    if locator.count():
        locator.first.fill(value)
        return
    locator = page.get_by_placeholder(re.compile(label, re.I))
    if locator.count():
        locator.first.fill(value)


def _select_labeled(page: Page, label: str, value: str) -> None:
    if not value:
        return
    locator = page.get_by_label(re.compile(rf"^{label}$", re.I))
    if locator.count():
        try:
            locator.first.select_option(label=value)
            return
        except Exception:
            try:
                locator.first.select_option(value=value)
                return
            except Exception:
                pass
    combo = page.get_by_role("combobox").filter(has_text=re.compile(label, re.I))
    if combo.count():
        combo.first.click()
        option = page.get_by_role("option", name=re.compile(rf"^{re.escape(value)}$", re.I))
        if option.count():
            option.first.click()


def _dismiss_modals(page: Page) -> None:
    for selector in (".swal2-confirm", ".swal2-close", "button.swal2-styled"):
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=2000)
                page.wait_for_timeout(400)
        except Exception:
            continue


def _click_login(page: Page) -> None:
    _dismiss_modals(page)
    login = page.locator("a.btn.login, a.login")
    if login.count():
        try:
            login.first.click(timeout=5000)
            return
        except PlaywrightTimeout:
            login.first.click(force=True)
            return
    for locator in (
        page.get_by_role("link", name=re.compile(r"^LOGIN$", re.I)),
        page.get_by_text(re.compile(r"^LOGIN$", re.I)),
        page.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in|submit", re.I)),
    ):
        if locator.count():
            locator.first.click(force=True)
            return
    page.locator('input[name="password"]').press("Enter")


def _hash(page: Page) -> str:
    return (page.url or "").split("#", 1)[-1].lower() if "#" in (page.url or "") else ""


def _on_login_page(page: Page) -> bool:
    return "login" in _hash(page) or (
        page.locator('input[name="username"]').count() > 0
        and page.locator("a.btn.search, #transactions-list tr[data-id]").count() == 0
    )


def _on_transactions_page(page: Page) -> bool:
    return page.locator("a.btn.search, #transactions-list tr[data-id]").count() > 0


def _unhide_2fa(page: Page) -> None:
    page.evaluate(
        """() => {
          const el = document.querySelector('input[name="passcode2fa"], input.passcode2fa');
          if (!el) return;
          el.style.setProperty('display', 'block', 'important');
          el.removeAttribute('hidden');
        }"""
    )


def _twofa_value(page: Page) -> str:
    raw = page.evaluate(
        """() => {
          const el = document.querySelector('input[name="passcode2fa"], input.passcode2fa');
          return el ? String(el.value || '') : '';
        }"""
    )
    return re.sub(r"\D", "", raw or "")


def _clear_2fa(page: Page) -> None:
    page.evaluate(
        """() => {
          const el = document.querySelector('input[name="passcode2fa"], input.passcode2fa');
          if (el) el.value = '';
        }"""
    )


def _login_wait_seconds(settings: Settings) -> int:
    if settings.manual_login_seconds:
        return max(settings.manual_login_seconds, 15)
    return 180 if settings.headed else 25


def _wait_for_app_ready(page: Page) -> None:
    try:
        page.locator(
            "#header, input[name='username'], a.btn.search, #transactions-list"
        ).first.wait_for(timeout=25000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(600)


def _goto_transactions(page: Page, settings: Settings) -> None:
    url = settings.dashboard_url or "https://skgaming16.as6868.com/#transactions"
    if "transactions" not in _hash(page):
        page.goto(url, wait_until="domcontentloaded")
        _wait_for_app_ready(page)


def _login_if_needed(page: Page, settings: Settings) -> None:
    page.wait_for_timeout(600)
    _dismiss_modals(page)
    if _on_transactions_page(page):
        return

    if not _on_login_page(page) and settings.dashboard_url:
        page.goto(
            settings.dashboard_url.replace("#transactions", "#login"),
            wait_until="domcontentloaded",
        )
        _wait_for_app_ready(page)
        _dismiss_modals(page)

    if _on_transactions_page(page):
        return

    user = page.locator('input[name="username"]')
    password = page.locator('input[name="password"]')
    if user.count() == 0 or password.count() == 0:
        return
    if not settings.dashboard_username or not settings.dashboard_password:
        raise ConfigError("Dashboard username or password is missing in the GUI login section.")

    user.first.fill(settings.dashboard_username)
    password.first.fill(settings.dashboard_password)
    _unhide_2fa(page)

    auto_code = re.sub(r"\D", "", settings.dashboard_2fa or "")
    if len(auto_code) >= 6:
        auto_code = auto_code[:6]
        page.evaluate(
            """(code) => {
              const el = document.querySelector('input[name="passcode2fa"], input.passcode2fa');
              if (!el) return;
              el.style.setProperty('display', 'block', 'important');
              el.value = code;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            auto_code,
        )
        _click_login(page)
        page.wait_for_timeout(2500)
        swal = page.evaluate(
            """() => {
              const t = document.querySelector('.swal2-title');
              const c = document.querySelector('.swal2-html-container, .swal2-content');
              return ((t && t.innerText) || '') + ' ' + ((c && c.innerText) || '');
            }"""
        )
        if "invalid" in (swal or "").lower():
            raise ConfigError(
                "The site rejected this login (Oops! Invalid Login). "
                "The 2FA code is a Google Authenticator value that changes every 30 seconds. "
                "Put the code showing in Authenticator right now into the GUI box and click Run now."
            )
    elif settings.headed:
        try:
            page.locator('input[name="passcode2fa"]').first.focus(timeout=2000)
        except Exception:
            pass

    deadline = time.monotonic() + _login_wait_seconds(settings)
    last_submit = 0.0
    while time.monotonic() < deadline:
        _dismiss_modals(page)
        if _on_transactions_page(page) or (
            "login" not in _hash(page) and page.locator('input[name="username"]').count() == 0
        ):
            _goto_transactions(page, settings)
            return
        if "login" not in _hash(page) and _on_transactions_page(page) is False:
            _goto_transactions(page, settings)
            if _on_transactions_page(page):
                return
        code = _twofa_value(page)
        if len(code) >= 6 and time.monotonic() - last_submit > 4:
            _click_login(page)
            last_submit = time.monotonic()
            page.wait_for_timeout(1200)
            swal = page.evaluate(
                """() => {
                  const t = document.querySelector('.swal2-title');
                  const c = document.querySelector('.swal2-html-container, .swal2-content');
                  return ((t && t.innerText) || '') + ' ' + ((c && c.innerText) || '');
                }"""
            )
            if "invalid" in (swal or "").lower():
                _dismiss_modals(page)
                _clear_2fa(page)
                last_submit = time.monotonic()
        page.wait_for_timeout(400)

    _dismiss_modals(page)
    if _on_transactions_page(page) or "login" not in _hash(page):
        _goto_transactions(page, settings)
        return
    raise ConfigError(
        "Login was not completed. Open Google Authenticator, type the current 6-digit "
        "code into 2FA Passcode, and click LOGIN in the browser window."
    )


def _wait_for_dashboard(page: Page, settings: Settings) -> None:
    if "#transactions" not in page.url and settings.dashboard_url:
        page.goto(settings.dashboard_url, wait_until="domcontentloaded")
    locator = page.locator("#transactions-list tr[data-id], .list-wrapper tr[data-id], a.btn.search")
    try:
        locator.first.wait_for(timeout=20000)
    except PlaywrightTimeout:
        if _on_login_page(page):
            raise ConfigError("Still on the login page after waiting for transactions.")


def _click_search(page: Page) -> None:
    for locator in (
        page.locator("a.btn.search, a.search"),
        page.get_by_text(re.compile(r"^SEARCH$", re.I)),
        page.get_by_role("button", name=re.compile(r"^SEARCH$", re.I)),
    ):
        if locator.count():
            locator.first.click()
            return


def _click_visible_calendar_day(page: Page, day: str) -> bool:
    wanted = (day or "").strip()
    if not wanted:
        return False
    day_number = wanted.split("-")[-1].lstrip("0") or "0"
    selectors = (
        "td.today:not(.old):not(.new)",
        "td.day.today",
        ".datepicker-days td.today",
        ".ui-datepicker-today a",
        ".flatpickr-day.today",
        "button.today",
    )
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=1500)
                return True
        except Exception:
            continue
    day_cells = page.locator(
        "td.day:not(.old):not(.new), .ui-datepicker-calendar td a, "
        ".flatpickr-day:not(.prevMonthDay):not(.nextMonthDay)"
    )
    try:
        count = day_cells.count()
    except Exception:
        count = 0
    for index in range(count):
        cell = day_cells.nth(index)
        try:
            if cell.is_visible() and (cell.inner_text() or "").strip() == day_number:
                cell.click(timeout=1500)
                return True
        except Exception:
            continue
    return False


def _select_filter_dates(page: Page, day: str, end: str) -> None:
    wanted = (day or "").strip()
    until = (end or day or "").strip()
    try:
        result = page.evaluate(SELECT_DATES_JS, {"from": wanted, "to": until})
    except Exception:
        result = {}
    states = list((result or {}).get("results") or [])
    if "opened" in states:
        page.wait_for_timeout(250)
        if not _click_visible_calendar_day(page, wanted):
            try:
                page.evaluate(SELECT_DATES_JS, {"from": wanted, "to": until})
            except Exception:
                pass
            page.wait_for_timeout(200)
            _click_visible_calendar_day(page, wanted)
    page.wait_for_timeout(150)


def _apply_filters(page: Page, settings: Settings) -> None:
    status = (settings.filter_status or COMPLETED_STATUS).strip() or COMPLETED_STATUS
    if status.upper() == "ANY":
        status = COMPLETED_STATUS
    day = settings.filter_date_from.strip() or local_today()
    end = settings.filter_date_to.strip() or day
    _select_labeled(page, "Status", status)
    try:
        page.evaluate(SET_STATUS_JS, status)
    except Exception:
        pass
    _select_filter_dates(page, day, end)
    _click_search(page)
    try:
        page.locator("#transactions-list tr[data-id]").first.wait_for(timeout=20000)
    except PlaywrightTimeout:
        try:
            page.get_by_text(re.compile(r"Record:\s*\d+", re.I)).first.wait_for(timeout=8000)
        except PlaywrightTimeout:
            page.wait_for_timeout(1500)
    try:
        page.locator(".pagination.simple-pagination, .simple-pagination").first.wait_for(timeout=8000)
    except PlaywrightTimeout:
        page.wait_for_timeout(800)


def _page_summary(page: Page) -> dict[str, int | str]:
    try:
        raw = page.evaluate(EXTRACT_SUMMARY_JS)
        if isinstance(raw, dict) and raw.get("records"):
            return {"records": int(raw.get("records") or 0), "total": str(raw.get("total") or "")}
    except Exception:
        pass
    try:
        return parse_website_summary(page.locator("body").inner_text())
    except Exception:
        return {"records": 0, "total": ""}


def _pager_state(page: Page) -> dict[str, int | list[str]]:
    try:
        raw = page.evaluate(PAGER_JS)
    except Exception:
        raw = {}
    labels = list(raw.get("labels") or [])
    hrefs = list(raw.get("hrefs") or [])
    current, last_from_labels = pager_bounds(labels, int(raw.get("current") or 0))
    last = max(int(raw.get("last") or 0), last_from_labels, pager_last_from_hrefs(hrefs, current))
    return {"current": current, "last": last, "labels": labels}


def _page_ids(page: Page) -> list[str]:
    try:
        raw = page.evaluate(PAGE_IDS_JS)
        return [str(item) for item in raw if item]
    except Exception:
        return []


def _goto_page(page: Page, number: int) -> bool:
    before = _page_ids(page)
    state = _pager_state(page)
    current = int(state.get("current") or 0)
    last = int(state.get("last") or 0)
    if current == number:
        return True
    if number < 1 or pager_finished(current, last) or (last and number > last):
        return False
    clicked = False
    try:
        clicked = bool(page.evaluate(CLICK_PAGER_JS, ["page", number]))
    except Exception:
        clicked = False
    if not clicked:
        link = page.locator(
            f'.pagination.simple-pagination a.page-link[href="#page-{number}"], '
            f'.simple-pagination a.page-link[href="#page-{number}"]'
        )
        if link.count():
            try:
                link.first.scroll_into_view_if_needed()
                link.first.click(timeout=3000)
                clicked = True
            except Exception:
                clicked = False
    if not clicked:
        return False
    try:
        page.wait_for_function(
            """old => {
              const ids = Array.from(document.querySelectorAll(
                "#transactions-list tr[data-id], .list-wrapper tr[data-id], table tr[data-id]"
              )).map((tr) => (tr.getAttribute("data-id") || "").trim()).filter(Boolean);
              return ids.length > 0 && ids.join(",") !== old;
            }""",
            arg=",".join(before),
            timeout=12000,
        )
    except PlaywrightTimeout:
        page.wait_for_timeout(1200)
        if _page_ids(page) == before:
            return False
    return True


def _goto_next_page(page: Page) -> bool:
    before = _page_ids(page)
    state = _pager_state(page)
    current = int(state.get("current") or 1)
    last = int(state.get("last") or 0)
    if pager_finished(current, last):
        return False
    clicked = False
    try:
        clicked = bool(page.evaluate(CLICK_PAGER_JS, ["next", ""]))
    except Exception:
        clicked = False
    if not clicked and last and current < last:
        try:
            clicked = bool(page.evaluate(CLICK_PAGER_JS, ["page", current + 1]))
        except Exception:
            clicked = False
    if not clicked:
        next_btn = page.locator(
            ".pagination.simple-pagination a.page-link.next, .simple-pagination a.page-link.next"
        )
        if next_btn.count():
            try:
                next_btn.last.scroll_into_view_if_needed()
                next_btn.last.click(timeout=3000)
                clicked = True
            except Exception:
                clicked = False
    if not clicked:
        return False
    try:
        page.wait_for_function(
            """old => {
              const ids = Array.from(document.querySelectorAll(
                "#transactions-list tr[data-id], .list-wrapper tr[data-id], table tr[data-id]"
              )).map((tr) => (tr.getAttribute("data-id") || "").trim()).filter(Boolean);
              return ids.length > 0 && ids.join(",") !== old;
            }""",
            arg=",".join(before),
            timeout=12000,
        )
    except PlaywrightTimeout:
        page.wait_for_timeout(1500)
        after = _page_ids(page)
        if after == before:
            return False
    return True


def _to_transaction(raw: dict) -> Transaction:
    return Transaction(
        transaction_id=str(raw.get("transaction_id") or "").strip(),
        username=str(raw.get("Username") or "").strip(),
        name=str(raw.get("Name") or "").strip(),
        mobile=str(raw.get("Mobile") or "").strip(),
        bank_account_name=str(
            raw.get("Bank Account Name") or raw.get("BankAccountName") or ""
        ).strip(),
        bank_account_number=str(
            raw.get("Bank Account Number") or raw.get("BankAccountNumber") or ""
        ).strip(),
        amount=str(raw.get("Amount") or "").replace(",", "").strip(),
        bank=str(raw.get("Bank") or "").strip(),
        method=str(raw.get("Method") or "").strip(),
        datetime=str(raw.get("Datetime") or "").strip(),
        gateway=str(raw.get("Gateway") or "").strip(),
        status=str(raw.get("status") or "").strip(),
        created=str(raw.get("created") or "").strip(),
        processed=str(raw.get("processed") or "").strip(),
        brand=captured_brand(str(raw.get("brand") or "")),
        bsb=str(raw.get("BankBSB") or "").strip(),
        pay_id=str(raw.get("PayID") or "").strip(),
        bank_lock=str(raw.get("BankLock") or "").strip(),
    )


@dataclass
class ScrapeCapture:
    transactions: list[Transaction] = field(default_factory=list)
    website_records: int = 0
    website_total: str = ""
    filter_date: str = ""
    filter_status: str = COMPLETED_STATUS


def scrape_transactions(
    settings: Settings,
    limit: int | None = None,
    on_event=None,
    once: bool = False,
) -> ScrapeCapture:
    settings.require_dashboard()
    settings.filter_status = COMPLETED_STATUS
    day = settings.filter_date_from.strip() or local_today()
    settings.filter_date_from = day
    settings.filter_date_to = settings.filter_date_to.strip() or day
    collected: dict[str, Transaction] = {}
    capture = ScrapeCapture(filter_date=day, filter_status=COMPLETED_STATUS)

    if settings.use_open_browser and not settings.dashboard_url:
        live_rows, page = scrape_open_browser()
        if page and page.url and not settings.dashboard_url:
            settings.dashboard_url = page.url
            persist_dashboard_url(page.url)
        for txn in live_rows:
            collected[txn.transaction_id] = txn
        if page and page.text:
            summary = parse_website_summary(page.text)
            capture.website_records = int(summary["records"] or 0)
            capture.website_total = str(summary["total"] or "")
        if collected:
            rows = list(collected.values())
            for txn in rows:
                extras = dict(txn.extras or {})
                extras["tally_date"] = day
                txn.extras = extras
            capture.transactions = rows[:limit] if limit else rows
            return capture

    if not settings.dashboard_url:
        raise ConfigError(
            "Could not read the open Chrome tab. Keep the admin dashboard visible, "
            "or paste DASHBOARD_URL in .env."
        )

    with sync_playwright() as playwright:
        launch_args = ["--disable-blink-features=AutomationControlled"]
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        launch_kwargs: dict = {
            "headless": not settings.headed,
            "slow_mo": settings.slow_mo_ms or 0,
            "args": launch_args,
        }
        if chrome.exists():
            launch_kwargs["executable_path"] = str(chrome)
        profile = ROOT / ".playwright-profile"
        profile.mkdir(exist_ok=True)
        context_kwargs = {
            "viewport": {"width": 1440, "height": 1100},
            "user_agent": _USER_AGENT,
            "locale": "en-AU",
            "timezone_id": "Australia/Melbourne",
        }
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                **launch_kwargs,
                **context_kwargs,
            )
            page = context.pages[0] if context.pages else context.new_page()
            owns_browser = False
        except Exception:
            browser = playwright.chromium.launch(**launch_kwargs)
            if settings.auth_state_path.exists():
                context_kwargs["storage_state"] = str(settings.auth_state_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            owns_browser = True
        else:
            browser = None
        try:
            if settings.headed:
                page.bring_to_front()
            page.goto(settings.dashboard_url, wait_until="domcontentloaded")
            _wait_for_app_ready(page)
            _dismiss_modals(page)
            if not _on_transactions_page(page):
                _login_if_needed(page, settings)
            _wait_for_dashboard(page, settings)
            _apply_filters(page, settings)
            summary = _page_summary(page)
            capture.website_records = int(summary.get("records") or 0)
            capture.website_total = str(summary.get("total") or "")
            state = _pager_state(page)
            per_page = max(len(_page_ids(page)), 1)
            last_page = max(
                int(state.get("last") or 0),
                estimated_pages(int(capture.website_records or 0), per_page),
            )
            if last_page:
                page_limit = last_page
            elif once:
                page_limit = 1
            else:
                page_limit = max(settings.max_pages, 80)
            _goto_page(page, 1)
            if on_event:
                on_event(
                    {
                        "kind": "log",
                        "message": (
                            f"Completed list shows Record: {capture.website_records or '?'} "
                            f"· about {per_page} per page · {last_page or '?'} page(s). "
                            + (
                                "Reading Completed once, then stopping."
                                if once
                                else "Reading every page so today's GUI count can match."
                            )
                        ),
                    }
                )

            for page_num in range(1, page_limit + 1):
                raw_cards = page.evaluate(EXTRACT_CARDS_JS)
                for raw in raw_cards:
                    txn = _to_transaction(raw)
                    if txn.transaction_id:
                        collected[txn.transaction_id] = txn
                state = _pager_state(page)
                current_page = int(state.get("current") or page_num)
                pager_last = int(state.get("last") or 0)
                last_page = max(
                    pager_last,
                    last_page,
                    estimated_pages(int(capture.website_records or 0), per_page),
                )
                if on_event:
                    on_event(
                        {
                            "kind": "log",
                            "message": (
                                f"Page {current_page}/{last_page or '?'} · "
                                f"{len(collected)} unique of "
                                f"{capture.website_records or '?'} Completed records."
                            ),
                        }
                    )
                if limit and len(collected) >= limit:
                    break
                if capture.website_records and len(collected) >= capture.website_records:
                    break
                if pager_finished(current_page, pager_last or last_page):
                    if on_event:
                        on_event(
                            {
                                "kind": "log",
                                "message": "Reached the last Completed page. Stopping this scrape.",
                            }
                        )
                    break
                next_page = page_num + 1
                if pager_last and next_page > pager_last:
                    break
                advanced = _goto_page(page, next_page)
                if not advanced:
                    advanced = _goto_next_page(page)
                if not advanced:
                    break

            if _on_transactions_page(page):
                context.storage_state(path=str(settings.auth_state_path))
        finally:
            context.close()
            if owns_browser and browser is not None:
                browser.close()

    rows = list(collected.values())
    for txn in rows:
        extras = dict(txn.extras or {})
        extras["tally_date"] = day
        txn.extras = extras
    capture.transactions = rows[:limit] if limit else rows
    return capture


def iter_preview(transactions: list[Transaction]) -> Iterator[Transaction]:
    yield from transactions
