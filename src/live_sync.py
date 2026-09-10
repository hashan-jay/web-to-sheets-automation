from __future__ import annotations

import threading
import time

from src.config import Settings
from src.dashboard_api import DashboardClient, dashboard_origin, post_with_page, scrape_via_http
from src.database import GatheringDB
from src.deposit_bank import fill_deposit_banks, sheet_bank_choices
from src.mapper import sheet_game_choices, to_sheet_row
from src.models import Transaction
from src.pipeline import EventFn, PipelineResult, _open_sheet, txn_row_event
from src.sheets import SheetClient
from src.tally import COMPLETED_STATUS, local_today

LIVE_INTERVAL = 1.0
LIVE_INTERVAL_MAX = 2.0


def live_interval_seconds(raw: object) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = LIVE_INTERVAL
    return min(LIVE_INTERVAL_MAX, max(LIVE_INTERVAL, value))


def _stamp(txn: Transaction, day: str) -> Transaction:
    extras = dict(txn.extras or {})
    extras["tally_date"] = day
    txn.extras = extras
    return txn


class LiveSheetWriter:
    def __init__(self, settings: Settings, on_event: EventFn | None) -> None:
        self.settings = settings
        self.on_event = on_event
        self.clients: list[SheetClient] = []
        self._ids: dict[tuple[int, str], set[str]] = {}
        self._games: dict[int, tuple[str, ...] | None] = {}
        for slot, sheet_id in settings.sheet_slots():
            try:
                client = _open_sheet(settings, slot, sheet_id)
            except Exception as exc:
                if on_event:
                    on_event(
                        {
                            "kind": "log",
                            "message": f"Sheet {slot}: live writer could not open this sheet: {exc}",
                        }
                    )
                continue
            self.clients.append(client)
            self._games[slot] = sheet_game_choices(
                settings, getattr(client, "sheet_id", ""), client.spreadsheet.title
            )

    def push(self, db: GatheringDB, txn: Transaction, day: str) -> None:
        if not self.clients:
            return
        for sheet in self.clients:
            key = (int(getattr(sheet, "slot", 0) or 0), day)
            try:
                sheet.use_day(day)
            except Exception as exc:
                db.mark(txn.transaction_id, "failed", str(exc))
                if self.on_event:
                    self.on_event(txn_row_event(txn, "Failed", str(exc)))
                continue
            if key not in self._ids:
                try:
                    self._ids[key] = sheet.existing_ids()
                except Exception as exc:
                    db.mark(txn.transaction_id, "failed", str(exc))
                    if self.on_event:
                        self.on_event(txn_row_event(txn, "Failed", str(exc)))
                    continue
            if txn.transaction_id in self._ids[key]:
                detail = f"Already on Google Sheet tab {sheet.tab_title()}"
                db.mark(txn.transaction_id, "skipped", detail)
                if self.on_event:
                    self.on_event(txn_row_event(txn, "Skipped", detail))
                continue
            games = self._games.get(int(getattr(sheet, "slot", 0) or 0))
            try:
                fill_deposit_banks(self.settings, [txn], self.on_event, choices=sheet_bank_choices(self.settings))
                sheet.write_row(to_sheet_row(txn, self.settings, games=games))
            except Exception as exc:
                db.mark(txn.transaction_id, "failed", str(exc))
                if self.on_event:
                    self.on_event(txn_row_event(txn, "Failed", str(exc)))
                continue
            self._ids[key].add(txn.transaction_id)
            detail = f"Row appended to Google Sheet tab {sheet.tab_title()}"
            db.mark(txn.transaction_id, "copied", detail)
            if self.on_event:
                self.on_event(txn_row_event(txn, "Copied", detail))


class LiveBrowser:
    """Keep one logged-in dashboard page open and POST the list the same way the site does."""

    def __init__(self, settings: Settings, on_event: EventFn | None = None) -> None:
        self.settings = settings
        self.on_event = on_event
        self.last_error = ""
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        from src.scraper import launch_dashboard_page

        if self.on_event:
            self.on_event(
                {
                    "kind": "log",
                    "message": (
                        "Opening a slim dashboard window for live Completed. "
                        "If login appears, enter the current 2FA code and click LOGIN."
                    ),
                }
            )
        self._playwright = sync_playwright().start()
        self.browser, self.context, self.page = launch_dashboard_page(
            self._playwright, self.settings, block_heavy=False
        )
        try:
            self.page.set_default_timeout(15000)
        except Exception:
            pass
        try:
            from src.scraper import SET_STATUS_JS

            self.page.evaluate(SET_STATUS_JS, COMPLETED_STATUS)
        except Exception:
            pass

    def post(self, path: str, data: dict[str, str]):
        if self.page is None:
            self.last_error = "dashboard page is not open"
            return None
        origin = dashboard_origin(self.settings.dashboard_url or self.page.url)
        payload, error = post_with_page(self.page, path, data, origin)
        self.last_error = error
        return payload

    def close(self) -> None:
        for closer in (self.context, self.browser):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self.page = None
        self.context = None
        self.browser = None
        self._playwright = None


def run_live_completed(
    settings: Settings,
    on_event: EventFn | None = None,
    write_sheet: bool = True,
    interval: float = LIVE_INTERVAL,
    stop_event: threading.Event | None = None,
) -> PipelineResult:
    stop = stop_event or threading.Event()
    interval = live_interval_seconds(interval)
    db = GatheringDB(settings.database_path)
    known = db.known_ids()
    settings.filter_status = COMPLETED_STATUS
    day = settings.filter_date_from.strip() or local_today()
    settings.filter_date_from = day
    settings.filter_date_to = settings.filter_date_to.strip() or day
    client: DashboardClient | LiveBrowser | None = DashboardClient.from_settings(settings)
    browser_live: LiveBrowser | None = None
    writer = LiveSheetWriter(settings, on_event) if write_sheet else None
    if write_sheet and writer is not None and not writer.clients:
        if on_event:
            on_event(
                {
                    "kind": "log",
                    "message": "Live GUI updates will continue, but no Google Sheet is open.",
                }
            )
        writer = None
    if on_event:
        on_event(
            {
                "kind": "log",
                "message": (
                    f"Live Completed started. Checking the dashboard every {interval:.0f}s. "
                    "New IDs appear in the GUI immediately"
                    + (
                        " and are written to the Google Sheet in the same second."
                        if writer
                        else "."
                    )
                ),
            }
        )
    if client is None and on_event:
        on_event(
            {
                "kind": "log",
                "message": (
                    "No saved HTTP login yet. Automated Run will open the dashboard "
                    "once, then keep Completed live."
                ),
            }
        )
    failures = 0
    last_records = -1
    last_beat = 0.0
    first_tick = True
    result = PipelineResult()
    try:
        while not stop.is_set():
            started = time.monotonic()
            settings.filter_status = COMPLETED_STATUS
            if client is None:
                try:
                    if browser_live is None:
                        browser_live = LiveBrowser(settings, on_event)
                        browser_live.start()
                    client = browser_live
                except Exception as exc:
                    failures += 1
                    if browser_live is not None:
                        browser_live.close()
                        browser_live = None
                    client = None
                    if on_event and (failures == 1 or failures % 10 == 0):
                        on_event(
                            {
                                "kind": "log",
                                "message": (
                                    f"Could not open the dashboard for live updates ({exc}). "
                                    "Uncheck Hide browser if 2FA is needed, then try Automated Run again."
                                ),
                            }
                        )
                    stop.wait(interval)
                    continue
            try:
                capture = scrape_via_http(
                    settings,
                    on_event=None,
                    known_ids=known,
                    catch_up=first_tick,
                    quiet=True,
                    client=client,
                    expect_new=0 if not first_tick else None,
                )
                if capture is None:
                    reason = getattr(client, "last_error", "") or "dashboard list HTTP failed"
                    raise RuntimeError(reason)
                grew = last_records >= 0 and capture.website_records > last_records
                if grew and not capture.transactions:
                    extra = scrape_via_http(
                        settings,
                        on_event=None,
                        known_ids=known,
                        catch_up=False,
                        quiet=True,
                        client=client,
                        expect_new=capture.website_records - last_records,
                    )
                    if extra and extra.transactions:
                        capture.transactions = extra.transactions
                        capture.pages += extra.pages
            except Exception as exc:
                failures += 1
                if browser_live is None:
                    if on_event:
                        on_event(
                            {
                                "kind": "log",
                                "message": (
                                    f"Direct HTTP live check failed ({exc}). "
                                    "Switching to a slim logged-in dashboard window."
                                ),
                            }
                        )
                    client = None
                    continue
                client = None
                if on_event and (failures == 1 or failures % 20 == 0):
                    on_event(
                        {
                            "kind": "log",
                            "message": (
                                f"Live Completed check failed ({exc}). "
                                "If the login page is showing, type the current 2FA code."
                            ),
                        }
                    )
                stop.wait(interval)
                continue
            failures = 0
            first_tick = False
            if capture.website_records != last_records:
                last_records = capture.website_records
                result.website_records = capture.website_records
                result.website_total = capture.website_total
                if on_event:
                    on_event(
                        {
                            "kind": "website_tally",
                            "records": capture.website_records,
                            "total": capture.website_total,
                            "date": day,
                            "status": COMPLETED_STATUS,
                            "scraped": len(known),
                        }
                    )
            new_rows = [
                _stamp(txn, day)
                for txn in capture.transactions
                if txn.transaction_id not in known
            ]
            if new_rows:
                inserted = db.ingest(new_rows, source="dashboard")
                result.scraped += len(new_rows)
                result.new_notifications += inserted
                for txn in new_rows:
                    known.add(txn.transaction_id)
                    if on_event:
                        on_event(txn_row_event(txn, "Gathered", "Live Completed"))
                if writer:
                    for txn in new_rows:
                        writer.push(db, txn, day)
                if on_event:
                    on_event(
                        {
                            "kind": "log",
                            "message": (
                                f"Live: {len(new_rows)} new Completed ID(s) "
                                f"for {day} · Record {capture.website_records}."
                            ),
                        }
                    )
                    on_event({"kind": "counts", "counts": db.counts()})
            now = time.monotonic()
            if on_event and now - last_beat >= 30:
                last_beat = now
                on_event(
                    {
                        "kind": "log",
                        "message": (
                            f"Live watching Completed · Record {capture.website_records or '?'} "
                            f"· next check in {interval:.0f}s."
                        ),
                    }
                )
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                stop.wait(remaining)
    finally:
        if browser_live is not None:
            browser_live.close()
            browser_live = None
    if on_event:
        on_event({"kind": "log", "message": "Live Completed stopped."})
        on_event({"kind": "done", "message": "Live Completed stopped.", "counts": db.counts()})
    return result
