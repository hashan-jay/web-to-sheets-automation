from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.config import Settings
from src.database import GatheringDB
from src.errors import ConfigError
from src.mapper import clean_name, resolve_brand, to_sheet_row
from src.models import Transaction
from src.scraper import scrape_transactions
from src.tally import COMPLETED_STATUS
from src.sheets import SheetClient, new_rows_only

EventFn = Callable[[dict], None]


@dataclass
class PipelineResult:
    scraped: int = 0
    new_notifications: int = 0
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    previewed: int = 0
    website_records: int = 0
    website_total: str = ""


def _emit(on_event: EventFn | None, **payload: object) -> None:
    if on_event:
        on_event(payload)


def _display_name(txn: Transaction) -> str:
    return clean_name(txn.bank_account_name or txn.name)


def _row_detail(txn: Transaction, message: str) -> str:
    extras = []
    if txn.status:
        extras.append(txn.status.title())
    if txn.bsb:
        extras.append(f"BSB {txn.bsb}")
    if txn.pay_id:
        extras.append(f"PayID {txn.pay_id}")
    prefix = " · ".join(extras)
    return f"{prefix} — {message}" if prefix else message


def txn_row_event(txn: Transaction, copy_status: str, detail: str) -> dict:
    return {
        "kind": "row",
        "transaction_id": txn.transaction_id,
        "username": txn.username,
        "name": _display_name(txn),
        "mobile": txn.mobile,
        "amount": txn.amount,
        "type": txn.status,
        "bank": txn.bank,
        "acc_name": txn.bank_account_name,
        "acc_no": txn.bank_account_number,
        "bsb": txn.bsb,
        "pay_id": txn.pay_id,
        "bank_lock": txn.bank_lock,
        "method": txn.method,
        "brand": resolve_brand(txn.brand, txn.name, default=""),
        "datetime": txn.datetime,
        "created": txn.created,
        "processed": txn.processed,
        "status": copy_status,
        "detail": detail,
    }


def gather_from_dashboard(
    settings: Settings,
    db: GatheringDB,
    on_event: EventFn | None = None,
    limit: int | None = None,
) -> PipelineResult:
    result = PipelineResult()
    _emit(on_event, kind="log", message="Gathering transactions from the dashboard...")
    if settings.headed:
        _emit(
            on_event,
            kind="log",
            message=(
                "A browser window will open. If you see the login page, type the "
                "current 6-digit Google Authenticator code into 2FA Passcode and click LOGIN."
            ),
        )
    capture = scrape_transactions(settings, limit=limit)
    transactions = capture.transactions
    result.scraped = len(transactions)
    result.website_records = capture.website_records
    result.website_total = capture.website_total
    result.new_notifications = db.ingest(transactions, source="dashboard")
    _emit(
        on_event,
        kind="website_tally",
        records=capture.website_records,
        total=capture.website_total,
        date=capture.filter_date,
        status=capture.filter_status or COMPLETED_STATUS,
        scraped=result.scraped,
    )
    if capture.website_records:
        _emit(
            on_event,
            kind="log",
            message=(
                f"Website Completed Record: {capture.website_records}"
                + (f" · Total {capture.website_total}" if capture.website_total else "")
                + f" · scraped {result.scraped} unique row(s) for {capture.filter_date}."
            ),
        )
    for txn in transactions:
        _emit(on_event, **txn_row_event(txn, "Gathered", "Read from #transactions-list"))
    _emit(
        on_event,
        kind="log",
        message=(
            f"Gathered {result.scraped} transaction(s); "
            f"{result.new_notifications} new notification(s) queued."
        ),
    )
    return result


def copy_pending_to_sheet(
    settings: Settings,
    db: GatheringDB,
    on_event: EventFn | None = None,
    dry_run: bool = False,
    only_ids: set[str] | None = None,
) -> PipelineResult:
    result = PipelineResult()
    pending = db.pending()
    if only_ids is not None:
        pending = [txn for txn in pending if txn.transaction_id in only_ids]
    if not pending:
        _emit(on_event, kind="log", message="No pending notifications in the gathering database.")
        return result

    existing_ids: set[str] = set()
    sheet: SheetClient | None = None
    if not dry_run:
        settings.require_sheets()
        sheet = SheetClient(
            settings.google_credentials_path,
            settings.google_sheet_id,
            settings.google_worksheet,
        )
        existing_ids = sheet.existing_ids()

    to_copy = pending if dry_run else new_rows_only(pending, existing_ids)
    skipped_ids = {txn.transaction_id for txn in pending} - {
        txn.transaction_id for txn in to_copy
    }
    for txn_id in skipped_ids:
        db.mark(txn_id, "skipped", "Already on the Google Sheet")
        result.skipped += 1
        _emit(
            on_event,
            kind="row",
            transaction_id=txn_id,
            name="",
            amount="",
            status="Skipped",
            detail="Already on the Google Sheet",
        )

    if dry_run:
        for txn in to_copy:
            result.previewed += 1
            _emit(
                on_event,
                **txn_row_event(
                    txn,
                    "Preview",
                    _row_detail(txn, "Dry run — sheet not updated; still pending"),
                ),
            )
        return result

    assert sheet is not None
    rows = [to_sheet_row(txn, settings) for txn in to_copy]
    for txn in to_copy:
        _emit(on_event, **txn_row_event(txn, "Copying", _row_detail(txn, "Writing row to Google Sheets")))
    try:
        sheet.write_rows(rows)
    except Exception as exc:
        for txn in to_copy:
            db.mark(txn.transaction_id, "failed", str(exc))
            result.failed += 1
            _emit(on_event, **txn_row_event(txn, "Failed", str(exc)))
        return result

    for txn in to_copy:
        db.mark(txn.transaction_id, "copied", "Copied to Google Sheets")
        result.copied += 1
        _emit(
            on_event,
            **txn_row_event(txn, "Copied", _row_detail(txn, "Row appended to Google Sheets")),
        )
    return result


def run_pipeline(
    settings: Settings,
    on_event: EventFn | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    scrape: bool = True,
    write_sheet: bool = False,
    only_ids: set[str] | None = None,
) -> PipelineResult:
    db = GatheringDB(settings.database_path)
    totals = PipelineResult()
    if scrape:
        if settings.use_open_browser:
            _emit(
                on_event,
                kind="log",
                message="Reading the admin website already open in Chrome/Brave...",
            )
        gathered = gather_from_dashboard(settings, db, on_event, limit=limit)
        totals.scraped = gathered.scraped
        totals.new_notifications = gathered.new_notifications
    if write_sheet:
        copied = copy_pending_to_sheet(
            settings,
            db,
            on_event,
            dry_run=dry_run,
            only_ids=only_ids,
        )
        totals.copied = copied.copied
        totals.skipped = copied.skipped
        totals.failed = copied.failed
        totals.previewed = copied.previewed
    _emit(
        on_event,
        kind="done",
        message=(
            f"Done. scraped={totals.scraped} new={totals.new_notifications} "
            f"copied={totals.copied} skipped={totals.skipped} "
            f"failed={totals.failed} preview={totals.previewed}"
        ),
        counts=db.counts(),
    )
    return totals


def process_new_notifications_only(
    settings: Settings,
    on_event: EventFn | None = None,
    dry_run: bool = False,
    only_ids: set[str] | None = None,
) -> PipelineResult:
    return run_pipeline(
        settings,
        on_event=on_event,
        dry_run=dry_run,
        scrape=False,
        write_sheet=True,
        only_ids=only_ids,
    )
