from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from src.config import Settings
from src.dashboard_api import (
    dashboard_origin,
    scrape_via_playwright_request,
)
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

DATE_TOOL_JS = r"""
(args) => {
  const action = String((args && args.action) || "info");
  const index = Number((args && args.index) || 0);
  const wanted = String((args && args.wanted) || "").trim();
  const norm = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const monthNames = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
  ];
  const shortNames = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

  const skip = (el) => {
    const hay = ((el.placeholder || "") + " " + (el.name || "") + " " +
      (el.id || "")).toLowerCase();
    return hay.includes("amount") || hay.includes("min") || hay.includes("max");
  };
  const looksLikeDate = (el) => {
    const hay = ((el.name || "") + " " + (el.id || "") + " " +
      (el.placeholder || "") + " " + (el.className || "") + " " +
      (el.type || "")).toLowerCase();
    return hay.includes("date") || el.type === "date";
  };
  const rowFields = () => {
    const nodes = Array.from(document.querySelectorAll("label, span, td, th, div, p, strong, dt"));
    for (const node of nodes) {
      if (!/^date\s*:?$/i.test(norm(node.textContent))) continue;
      const row = node.closest("tr, .form-group, .form-item, .row, li, dl") || node.parentElement;
      if (!row) continue;
      const inputs = Array.from(row.querySelectorAll("input")).filter((el) => !skip(el));
      if (inputs.length) return inputs.slice(0, 2);
    }
    return [];
  };
  const dateFields = () => {
    const scoped = rowFields();
    if (scoped.length) return scoped;
    return Array.from(document.querySelectorAll("input")).filter(
      (el) => !skip(el) && looksLikeDate(el)
    );
  };

  const parts = (() => {
    const bits = wanted.split("-").map(Number);
    if (bits.length !== 3 || bits.some((n) => !n)) return null;
    return { year: bits[0], month: bits[1], day: bits[2] };
  })();
  const ymd = (raw) => {
    const match = String(raw || "").match(/(\d{4}-\d{2}-\d{2})/);
    return match ? match[1] : "";
  };
  const valueMatches = (el) => {
    if (!el || !wanted) return false;
    const raw = norm(el.value);
    const tight = raw.replace(/\s+/g, "");
    if (/^(\d{4}-\d{2}-\d{2})\1$/.test(tight)) return false;
    return ymd(raw) === wanted && tight.length <= 10;
  };

  const fields = dateFields();
  if (action === "info") {
    return {
      count: fields.length,
      values: fields.map((el) => norm(el.value)),
      placeholders: fields.map((el) => norm(el.placeholder)),
      types: fields.map((el) => String(el.type || "text").toLowerCase()),
      readonly: fields.map((el) => Boolean(el.readOnly)),
    };
  }

  const el = fields[index];
  if (!el) return { ok: false, reason: "no-field" };
  const fire = (node) => {
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  };

  if (action === "clear") {
    el.value = "";
    fire(el);
    return { ok: true, value: norm(el.value) };
  }

  if (action === "restore") {
    el.value = String((args && args.text) || "");
    fire(el);
    return { ok: true, value: norm(el.value) };
  }

  if (action === "open") {
    el.scrollIntoView({ block: "center", inline: "nearest" });
    el.focus();
    el.click();
    return { ok: true, type: String(el.type || "text").toLowerCase(), matched: valueMatches(el) };
  }

  if (action === "check") {
    return { ok: valueMatches(el), value: norm(el.value) };
  }

  if (action === "widget") {
    if (!parts) return { ok: false, reason: "bad-date" };
    const jq = window.jQuery || window.$;
    const stamp = new Date(parts.year, parts.month - 1, parts.day);
    if (String(el.type || "").toLowerCase() === "date") {
      // A native date field has no DOM calendar to click; ISO assignment is exact.
      el.value = wanted;
      fire(el);
      if (valueMatches(el)) return { ok: true, how: "native" };
    }
    if (el._flatpickr) {
      try {
        el._flatpickr.setDate(stamp, true);
        if (valueMatches(el)) return { ok: true, how: "flatpickr" };
      } catch (err) {}
    }
    if (jq && jq.fn) {
      const $el = jq(el);
      try {
        const drp = $el.data("daterangepicker");
        if (drp) {
          drp.setStartDate(stamp);
          drp.setEndDate(stamp);
          if (valueMatches(el)) return { ok: true, how: "daterangepicker" };
        }
      } catch (err) {}
      try {
        if ($el.data("datepicker")) {
          $el.datepicker("update", stamp);
          if (valueMatches(el)) return { ok: true, how: "bootstrap-datepicker" };
        }
      } catch (err) {}
      try {
        if ($el.hasClass("hasDatepicker")) {
          $el.datepicker("setDate", stamp);
          if (valueMatches(el)) return { ok: true, how: "jquery-ui" };
        }
      } catch (err) {}
    }
    return { ok: valueMatches(el), how: "none" };
  }

  if (action !== "pick") return { ok: false, reason: "unknown-action" };
  if (!parts) return { ok: false, reason: "bad-date" };

  const visible = (node) => {
    if (!node || !node.getBoundingClientRect) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width < 90 || rect.height < 90) return false;
    const style = window.getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return false;
    return Number(style.opacity || "1") > 0.05;
  };
  const isLeaf = (node) => node.children.length === 0;
  const dayCells = (root) => Array.from(
    root.querySelectorAll("td, a, span, div, button")
  ).filter((cell) => isLeaf(cell) && /^\d{1,2}$/.test(norm(cell.textContent)));

  const containers = Array.from(document.querySelectorAll("div, table, section, ul"))
    .filter(visible)
    .map((node) => ({ node: node, cells: dayCells(node).length }))
    .filter((item) => item.cells >= 20 && item.cells <= 120)
    .sort((a, b) => a.node.querySelectorAll("*").length - b.node.querySelectorAll("*").length);
  if (!containers.length) return { ok: false, reason: "no-calendar" };

  const readTitle = (root) => {
    const monthSelect = root.querySelector("select.monthselect, select.ui-datepicker-month");
    const yearSelect = root.querySelector("select.yearselect, select.ui-datepicker-year");
    if (monthSelect && yearSelect) {
      return {
        year: Number(yearSelect.value),
        month: Number(monthSelect.value) + 1,
        monthSelect: monthSelect,
        yearSelect: yearSelect,
      };
    }
    const nodes = Array.from(root.querySelectorAll("th, div, span, button, a, h1, h2, h3, h4"));
    for (const node of nodes) {
      const text = norm(node.textContent).toLowerCase();
      if (!text || text.length > 40) continue;
      const yearMatch = text.match(/(20\d{2})/);
      if (!yearMatch) continue;
      let month = 0;
      for (let i = 0; i < 12; i++) {
        if (text.includes(monthNames[i])) { month = i + 1; break; }
      }
      if (!month) {
        for (let i = 0; i < 12; i++) {
          if (text.includes(shortNames[i])) { month = i + 1; break; }
        }
      }
      if (month) return { year: Number(yearMatch[1]), month: month };
    }
    return null;
  };
  const navButton = (root, direction) => {
    const keys = direction < 0 ? ["prev", "previous"] : ["next"];
    const glyphs = direction < 0
      ? ["\u2039", "\u00ab", "<"]
      : ["\u203a", "\u00bb", ">"];
    const nodes = Array.from(root.querySelectorAll("th, a, span, button, div, i"));
    const byName = nodes.find((node) => {
      if (/^\d{1,2}$/.test(norm(node.textContent))) return false;
      const hay = (String(node.className || "") + " " +
        (node.getAttribute("aria-label") || "")).toLowerCase();
      return keys.some((key) => hay.includes(key));
    });
    if (byName) return byName;
    return nodes.find(
      (node) => isLeaf(node) && glyphs.indexOf(norm(node.textContent)) >= 0
    ) || null;
  };

  const titleScope = (node) => {
    let current = node;
    for (let up = 0; up < 5 && current; up++) {
      if (readTitle(current)) return current;
      current = current.parentElement;
    }
    return null;
  };

  const blocked = /(^|[\s-])(old|new|off|disabled|other-?month|outside|muted|unavailable|prevmonthday|nextmonthday)([\s-]|$)/i;
  for (const item of containers) {
    const root = titleScope(item.node);
    if (!root) continue;
    for (let step = 0; step < 30; step++) {
      const title = readTitle(root);
      if (!title) break;
      if (title.year === parts.year && title.month === parts.month) break;
      if (title.monthSelect && title.yearSelect) {
        title.yearSelect.value = String(parts.year);
        title.yearSelect.dispatchEvent(new Event("change", { bubbles: true }));
        title.monthSelect.value = String(parts.month - 1);
        title.monthSelect.dispatchEvent(new Event("change", { bubbles: true }));
        continue;
      }
      const back = (title.year * 12 + title.month) > (parts.year * 12 + parts.month);
      const button = navButton(root, back ? -1 : 1);
      if (!button) break;
      button.click();
    }
    const title = readTitle(root);
    if (!title || title.year !== parts.year || title.month !== parts.month) continue;
    const cell = dayCells(root).find((node) => {
      if (norm(node.textContent) !== String(parts.day)) return false;
      const own = String(node.className || "");
      const parent = String((node.parentElement && node.parentElement.className) || "");
      if (blocked.test(own) || blocked.test(parent)) return false;
      if (node.hasAttribute("disabled")) return false;
      return node.getAttribute("aria-disabled") !== "true";
    });
    if (!cell) continue;
    cell.scrollIntoView({ block: "center", inline: "nearest" });
    cell.click();
    return { ok: valueMatches(el), clicked: true, value: norm(el.value) };
  }
  return { ok: valueMatches(el), clicked: false, value: norm(el.value) };
}
"""

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_LIGHT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--no-first-run",
    "--mute-audio",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-features=TranslateUI,BackForwardCache,AcceptCHFrame,MediaRouter,InterestFeedContentSuggestions",
    "--renderer-process-limit=2",
    "--js-flags=--max-old-space-size=256",
]
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
_BLOCKED_URL_PARTS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "facebook.net",
    "hotjar",
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
    const attachEl = Array.from(tr.querySelectorAll("a, button, span, img")).find((el) =>
      /attachment|receipt|proof|slip/i.test(
        (el.textContent || "") + " " + (el.getAttribute("href") || "") + " " +
        (el.getAttribute("src") || "") + " " + (el.getAttribute("title") || "") + " " +
        (el.getAttribute("onclick") || "")
      )
    );
    if (attachEl) {
      const blob = [
        attachEl.getAttribute("onclick") || "",
        attachEl.getAttribute("href") || "",
        attachEl.getAttribute("data-url") || "",
        attachEl.getAttribute("data-src") || "",
        attachEl.getAttribute("data-file") || "",
        attachEl.getAttribute("data-attachment") || "",
        attachEl.getAttribute("src") || "",
        attachEl.href || "",
      ].join(" ");
      const fromClick = blob.match(/https?:\/\/[^'"\s)]+|\/[^'"\s)]+\.(?:png|jpe?g|webp|gif|bmp|pdf)/i);
      data.attachment = (fromClick && fromClick[0])
        || attachEl.getAttribute("href")
        || attachEl.getAttribute("data-url")
        || attachEl.getAttribute("src")
        || "";
    }
    if (!data.attachment) {
      const img = tr.querySelector("img[src]");
      if (img) data.attachment = img.getAttribute("src") || "";
    }
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
    page.wait_for_timeout(200)


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


def _date_tool(
    page: Page, action: str, index: int = 0, wanted: str = "", text: str = ""
) -> dict:
    try:
        raw = page.evaluate(
            DATE_TOOL_JS,
            {"action": action, "index": index, "wanted": wanted, "text": text},
        )
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _date_fields(page: Page) -> dict:
    return _date_tool(page, "info")


def _date_index_matches(page: Page, index: int, wanted: str) -> bool:
    return bool(_date_tool(page, "check", index, wanted).get("ok"))


def _type_date_at_index(page: Page, index: int, wanted: str) -> bool:
    """Last resort when no calendar opens: clear the field, then enter the date once."""
    if not _date_tool(page, "open", index, wanted).get("ok"):
        return False
    try:
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(wanted, delay=40)
        page.wait_for_timeout(200)
    except Exception:
        return False
    return _date_index_matches(page, index, wanted)


def _pick_date_at_index(
    page: Page, index: int, wanted: str, readonly: bool = False
) -> bool:
    if not wanted:
        return False
    if _date_index_matches(page, index, wanted):
        return True
    values = _date_fields(page).get("values") or []
    original = str(values[index]) if index < len(values) else ""
    for _attempt in range(3):
        _date_tool(page, "clear", index, wanted)
        if not _date_tool(page, "open", index, wanted).get("ok"):
            break
        page.wait_for_timeout(280)
        if _date_tool(page, "pick", index, wanted).get("ok"):
            return True
        if _date_tool(page, "widget", index, wanted).get("ok"):
            return True
    if not readonly and _type_date_at_index(page, index, wanted):
        return True
    if original and not _date_index_matches(page, index, wanted):
        _date_tool(page, "restore", index, wanted, text=original)
    return False


def _select_filter_dates(page: Page, day: str, end: str, on_event=None) -> None:
    wanted = (day or "").strip()
    until = (end or day or "").strip()
    fields = _date_fields(page)
    locked = list(fields.get("readonly") or [])
    count = int(fields.get("count") or 0)
    for index, value in ((0, wanted), (1, until)):
        if index >= count:
            continue
        _pick_date_at_index(
            page,
            index,
            value,
            readonly=index < len(locked) and bool(locked[index]),
        )
    page.wait_for_timeout(150)
    if on_event:
        final = [str(item or "") for item in _date_fields(page).get("values") or []]
        start_value = final[0] if final else ""
        end_value = final[1] if len(final) > 1 else ""
        on_event(
            {
                "kind": "log",
                "message": (
                    f"Date filter selected on the website: start {start_value or '(blank)'} "
                    f"· end {end_value or '(blank)'} (wanted {wanted})."
                ),
            }
        )


def _apply_filters(page: Page, settings: Settings, on_event=None) -> None:
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
    _select_filter_dates(page, day, end, on_event=on_event)
    if on_event:
        on_event(
            {
                "kind": "log",
                "message": (
                    f"Website filters set to Status {status} · date {day}"
                    + (f" to {end}" if end and end != day else "")
                    + ". Searching every Completed page."
                ),
            }
        )
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
        attachment=str(raw.get("attachment") or "").strip(),
        extras={"attachment": str(raw.get("attachment") or "").strip()}
        if str(raw.get("attachment") or "").strip()
        else {},
    )


@dataclass
class ScrapeCapture:
    transactions: list[Transaction] = field(default_factory=list)
    website_records: int = 0
    website_total: str = ""
    filter_date: str = ""
    filter_status: str = COMPLETED_STATUS


def _stamp_tally_date(rows: list[Transaction], day: str) -> list[Transaction]:
    for txn in rows:
        extras = dict(txn.extras or {})
        extras["tally_date"] = day
        txn.extras = extras
    return rows


def _capture_from_api(api, day: str, limit: int | None) -> ScrapeCapture | None:
    if api is None:
        return None
    rows = _stamp_tally_date(list(api.transactions), day)
    if limit:
        rows = rows[:limit]
    return ScrapeCapture(
        transactions=rows,
        website_records=int(api.website_records or 0),
        website_total=str(api.website_total or ""),
        filter_date=day,
        filter_status=COMPLETED_STATUS,
    )


def _block_heavy_resources(route) -> None:
    try:
        request = route.request
        url = (request.url or "").lower()
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            route.abort()
            return
        if any(part in url for part in _BLOCKED_URL_PARTS):
            route.abort()
            return
        route.continue_()
    except Exception:
        try:
            route.continue_()
        except Exception:
            try:
                route.abort()
            except Exception:
                pass


LOADING_IDLE_JS = """() => !window.jQuery || jQuery.active === 0"""

HIDE_LOADING_JS = """() => {
  document.querySelectorAll(".blockUI, .pace-active, .loading-overlay").forEach((el) => {
    el.style.display = "none";
  });
  if (window.jQuery) {
    try { jQuery(".blockUI").hide(); } catch (err) {}
  }
}"""


def _wait_for_loading_idle(page: Page) -> None:
    try:
        page.wait_for_function(LOADING_IDLE_JS, timeout=4000)
        return
    except Exception:
        pass
    try:
        page.evaluate(HIDE_LOADING_JS)
    except Exception:
        pass


def _read_admin_token(page: Page) -> str:
    try:
        raw = page.evaluate(
            """() => {
              try {
                const admin = JSON.parse(localStorage.getItem('ADMIN') || '{}') || {};
                return String(admin.token || '');
              } catch (err) {
                return '';
              }
            }"""
        )
    except Exception:
        return ""
    return str(raw or "").strip()


def _try_api_from_context(context: BrowserContext, page: Page, settings: Settings, limit, on_event):
    origin = dashboard_origin(settings.dashboard_url or page.url)
    token = _read_admin_token(page)
    return scrape_via_playwright_request(
        context.request,
        origin,
        token,
        settings,
        limit=limit,
        on_event=on_event,
    )


def launch_dashboard_page(playwright, settings: Settings, block_heavy: bool = True):
    launch_kwargs: dict = {
        "headless": not settings.headed,
        "slow_mo": settings.slow_mo_ms or 0,
        "args": list(_LIGHT_ARGS),
    }
    context_kwargs = {
        "viewport": {"width": 1100, "height": 720},
        "user_agent": _USER_AGENT,
        "locale": "en-AU",
        "timezone_id": "Australia/Melbourne",
    }
    if settings.auth_state_path.exists():
        context_kwargs["storage_state"] = str(settings.auth_state_path)
    browser = playwright.chromium.launch(**launch_kwargs)
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    if block_heavy:
        page.route("**/*", _block_heavy_resources)
    if settings.headed:
        page.bring_to_front()
    page.goto(settings.dashboard_url, wait_until="domcontentloaded")
    _wait_for_app_ready(page)
    _dismiss_modals(page)
    if not _on_transactions_page(page):
        _login_if_needed(page, settings)
    _wait_for_dashboard(page, settings)
    _wait_for_loading_idle(page)
    if _on_transactions_page(page):
        context.storage_state(path=str(settings.auth_state_path))
    return browser, context, page


class DashboardSession:
    """Keep one slim Chromium open so Automated Run does not relaunch Chrome every tick."""

    def __init__(self) -> None:
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._key = ""

    def start(self, settings: Settings):
        from playwright.sync_api import sync_playwright

        key = (
            f"{settings.dashboard_url.strip()}|{settings.dashboard_username.strip()}|"
            f"{int(bool(settings.headed))}"
        )
        if self.page is not None and key != self._key:
            self._reset_browser()
        if self.page is not None:
            try:
                if not self.page.is_closed():
                    if not _on_transactions_page(self.page):
                        _login_if_needed(self.page, settings)
                        _wait_for_dashboard(self.page, settings)
                    return self.browser, self.context, self.page
            except Exception:
                self._reset_browser()
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        self.browser, self.context, self.page = launch_dashboard_page(
            self._playwright, settings, block_heavy=True
        )
        self._key = key
        return self.browser, self.context, self.page

    def _reset_browser(self) -> None:
        for closer in (self.context, self.browser):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                pass
        self.page = None
        self.context = None
        self.browser = None
        self._key = ""

    def close(self) -> None:
        self._reset_browser()
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def scrape_transactions(
    settings: Settings,
    limit: int | None = None,
    on_event=None,
    once: bool = False,
    session: DashboardSession | None = None,
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
            capture.transactions = _stamp_tally_date(rows[:limit] if limit else rows, day)
            return capture

    if not settings.dashboard_url:
        raise ConfigError(
            "Could not read the open Chrome tab. Keep the admin dashboard visible, "
            "or paste DASHBOARD_URL in .env."
        )

    def _scrape_with(browser, context, page, close_browser: bool) -> None:
        try:
            _apply_filters(page, settings, on_event=on_event)
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
            if close_browser:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

    if session is not None:
        browser, context, page = session.start(settings)
        _scrape_with(browser, context, page, close_browser=False)
    else:
        with sync_playwright() as playwright:
            browser, context, page = launch_dashboard_page(playwright, settings)
            _scrape_with(browser, context, page, close_browser=True)

    rows = _stamp_tally_date(list(collected.values()), day)
    capture.transactions = rows[:limit] if limit else rows
    return capture


def iter_preview(transactions: list[Transaction]) -> Iterator[Transaction]:
    yield from transactions
