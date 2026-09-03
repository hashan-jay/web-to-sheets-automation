from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.config import Settings
from src.database import GatheringDB, _transaction_from_payload
from src.mapper import (
    captured_brand,
    clean_name,
    sheet_game_choices,
    to_sheet_row,
    txn_local_date,
)
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


def _sheet_label(sheet: SheetClient) -> str:
    slot = int(getattr(sheet, "slot", 0) or 0)
    title = sheet.spreadsheet.title
    return f"Sheet {slot} ({title})" if slot else title


def _open_sheet(settings: Settings, slot: int, sheet_id: str) -> SheetClient:
    sheet = SheetClient(
        settings.google_credentials_path,
        sheet_id,
        settings.google_worksheet,
    )
    sheet.slot = slot
    return sheet


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
        "brand": captured_brand(txn.brand),
        "datetime": txn.datetime,
        "created": txn.created,
        "processed": txn.processed,
        "tally_date": (txn.extras or {}).get("tally_date") or txn_local_date(txn),
        "status": copy_status,
        "detail": detail,
    }


def gather_from_dashboard(
    settings: Settings,
    db: GatheringDB,
    on_event: EventFn | None = None,
    limit: int | None = None,
    once: bool = False,
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
    capture = scrape_transactions(settings, limit=limit, on_event=on_event, once=once)
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
    branded = [txn.brand for txn in transactions if (txn.brand or "").strip()]
    missing_brand = result.scraped - len(branded)
    if branded:
        sample = ", ".join(sorted(set(branded))[:8])
        _emit(
            on_event,
            kind="log",
            message=(
                f"Brand captured on {len(branded)}/{result.scraped} row(s): {sample}."
            ),
        )
    if missing_brand:
        _emit(
            on_event,
            kind="log",
            message=f"{missing_brand} row(s) had no brand badge on the dashboard.",
        )
    _emit(
        on_event,
        kind="log",
        message=(
            f"Gathered {result.scraped} transaction(s); "
            f"{result.new_notifications} new notification(s) queued."
        ),
    )
    remaining = max(0, int(capture.website_records or 0) - result.scraped)
    if once:
        _emit(
            on_event,
            kind="remaining",
            remaining=remaining,
            scraped=result.scraped,
            records=capture.website_records,
            date=capture.filter_date,
        )
        if result.scraped == 0:
            _emit(
                on_event,
                kind="log",
                message=(
                    f"No Completed transactions were available for {capture.filter_date}. "
                    "Run now stopped after one pass."
                ),
            )
        elif remaining:
            _emit(
                on_event,
                kind="log",
                message=(
                    f"Run now finished after one Completed pass. "
                    f"Extracted {result.scraped} of website Record {capture.website_records}. "
                    f"Remaining needed: {remaining}. Automation stopped."
                ),
            )
        else:
            _emit(
                on_event,
                kind="log",
                message=(
                    f"Run now finished after one Completed pass. "
                    f"Extracted {result.scraped} transaction(s). "
                    "No remaining needed. Automation stopped."
                ),
            )
    elif capture.website_records and result.scraped < capture.website_records:
        _emit(
            on_event,
            kind="log",
            message=(
                f"Extracted {result.scraped} of website Record {capture.website_records}. "
                "Some Completed pages were not read. Run now again with the browser visible."
            ),
        )
    return result


def copy_pending_to_sheet(
    settings: Settings,
    db: GatheringDB,
    on_event: EventFn | None = None,
    dry_run: bool = False,
    only_ids: set[str] | None = None,
    one_by_one: bool = False,
) -> PipelineResult:
    result = PipelineResult()
    pending = db.pending()
    if only_ids is not None:
        pending = [txn for txn in pending if txn.transaction_id in only_ids]
    if not pending:
        _emit(on_event, kind="log", message="No pending notifications in the gathering database.")
        return result

    if dry_run:
        for txn in pending:
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

    settings.require_sheets()
    groups = _group_by_day(pending, settings.filter_date_from)
    targets = settings.sheet_slots()
    _emit(
        on_event,
        kind="log",
        message="Send will write the same rows to "
        + ", ".join(f"Sheet {slot}" for slot, _sheet_id in targets)
        + ".",
    )
    for slot, sheet_id in targets:
        try:
            sheet = _open_sheet(settings, slot, sheet_id)
        except Exception as exc:
            _emit(
                on_event,
                kind="log",
                message=f"Sheet {slot}: could not open this Google Sheet: {exc}",
            )
            continue
        _emit(
            on_event,
            kind="log",
            message=f"{_sheet_label(sheet)}: sending rows.",
        )
        for day, txns in groups.items():
            _write_day_rows(
                settings,
                db,
                sheet,
                day,
                txns,
                result,
                on_event,
                action="Copied",
                one_by_one=one_by_one,
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
    once: bool = False,
    one_by_one: bool = False,
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
        gathered = gather_from_dashboard(settings, db, on_event, limit=limit, once=once)
        totals.scraped = gathered.scraped
        totals.new_notifications = gathered.new_notifications
    if write_sheet:
        copied = copy_pending_to_sheet(
            settings,
            db,
            on_event,
            dry_run=dry_run,
            only_ids=only_ids,
            one_by_one=one_by_one,
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


def _unique_transactions(rows: list[Transaction]) -> list[Transaction]:
    seen: set[str] = set()
    unique: list[Transaction] = []
    for txn in rows:
        if not txn.transaction_id or txn.transaction_id in seen:
            continue
        seen.add(txn.transaction_id)
        unique.append(txn)
    return unique


def transactions_for_date(db: GatheringDB, day: str) -> list[Transaction]:
    collected: list[Transaction] = []
    for row in db.all_records():
        payload = row.get("payload_json")
        if not payload:
            continue
        txn = _transaction_from_payload(payload)
        record_day = txn_local_date(txn) or str(row.get("created_at") or "")[:10]
        if day not in {"", "All dates"} and record_day != day:
            continue
        collected.append(txn)
    return _unique_transactions(collected)


def sync_date_to_sheet(
    settings: Settings,
    day: str,
    on_event: EventFn | None = None,
) -> PipelineResult:
    result = PipelineResult()
    db = GatheringDB(settings.database_path)
    candidates = transactions_for_date(db, day)
    if not candidates:
        _emit(
            on_event,
            kind="log",
            message=f"No GUI records for {day}. Run now for that date first.",
        )
        _emit(on_event, kind="done", message=f"Sync found no GUI records for {day}.", counts=db.counts())
        return result

    settings.require_sheets()
    groups = (
        _group_by_day(candidates, settings.filter_date_from)
        if day in {"", "All dates"}
        else {day: candidates}
    )
    targets = settings.sheet_slots()
    _emit(
        on_event,
        kind="log",
        message="Sync will update "
        + ", ".join(f"Sheet {slot}" for slot, _sheet_id in targets)
        + ".",
    )
    for slot, sheet_id in targets:
        try:
            sheet = _open_sheet(settings, slot, sheet_id)
        except Exception as exc:
            _emit(
                on_event,
                kind="log",
                message=f"Sheet {slot}: could not open this Google Sheet: {exc}",
            )
            continue
        _emit(
            on_event,
            kind="log",
            message=f"{_sheet_label(sheet)}: sync started.",
        )
        try:
            for one_day, txns in groups.items():
                _sync_one_day(settings, db, sheet, one_day, txns, result, on_event)
        except Exception as exc:
            _emit(
                on_event,
                kind="log",
                message=f"{_sheet_label(sheet)}: sync failed: {exc}",
            )
            continue
        _emit(
            on_event,
            kind="log",
            message=f"{_sheet_label(sheet)}: sync finished.",
        )
    _emit(
        on_event,
        kind="done",
        message=f"Sync complete for {day}. restored={result.copied}",
        counts=db.counts(),
    )
    return result


def _group_by_day(transactions: list[Transaction], fallback: str) -> dict[str, list[Transaction]]:
    groups: dict[str, list[Transaction]] = {}
    for txn in transactions:
        record_day = txn_local_date(txn) or fallback
        groups.setdefault(record_day, []).append(txn)
    return groups


def _blank_sheet_bank(sheet: SheetClient, on_event: EventFn | None) -> None:
    try:
        cleared = sheet.clear_bank_names()
    except Exception as exc:
        _emit(
            on_event,
            kind="log",
            message=f"{_sheet_label(sheet)}: could not clear Bank on tab {sheet.tab_title()}: {exc}",
        )
        return
    if cleared:
        _emit(
            on_event,
            kind="log",
            message=(
                f"{_sheet_label(sheet)}: left Bank blank on {cleared} "
                f"deposit and withdraw row(s) of tab {sheet.tab_title()}."
            ),
        )


def _write_day_rows(
    settings: Settings,
    db: GatheringDB,
    sheet: SheetClient,
    day: str,
    txns: list[Transaction],
    result: PipelineResult,
    on_event: EventFn | None,
    action: str,
    one_by_one: bool = False,
) -> None:
    try:
        sheet.use_day(day)
    except Exception as exc:
        for txn in txns:
            db.mark(txn.transaction_id, "failed", str(exc))
            result.failed += 1
            _emit(on_event, **txn_row_event(txn, "Failed", str(exc)))
        return
    existing_ids = sheet.existing_ids()
    to_copy = new_rows_only(txns, existing_ids)
    skipped_ids = {txn.transaction_id for txn in txns} - {
        txn.transaction_id for txn in to_copy
    }
    for txn_id in skipped_ids:
        db.mark(txn_id, "skipped", f"Already on Google Sheet tab {sheet.tab_title()}")
        result.skipped += 1
        _emit(
            on_event,
            kind="row",
            transaction_id=txn_id,
            name="",
            amount="",
            status="Skipped",
            detail=f"Already on Google Sheet tab {sheet.tab_title()}",
        )
    if not to_copy:
        _blank_sheet_bank(sheet, on_event)
        return
    _emit(
        on_event,
        kind="log",
        message=(
            f"{_sheet_label(sheet)}: writing {len(to_copy)} row(s) "
            f"{'one by one ' if one_by_one else ''}"
            f"to tab {sheet.tab_title()}."
        ),
    )
    games = sheet_game_choices(
        settings,
        getattr(sheet, "sheet_id", ""),
        sheet.spreadsheet.title,
    )
    if games:
        _emit(
            on_event,
            kind="log",
            message=(
                f"{_sheet_label(sheet)}: using GROUP D game dropdown "
                + ", ".join(games)
                + "."
            ),
        )
    detail = (
        f"Row appended to Google Sheet tab {sheet.tab_title()}"
        if action == "Copied"
        else f"Missing row restored to Google Sheet tab {sheet.tab_title()}"
    )
    if one_by_one:
        for txn in to_copy:
            _emit(
                on_event,
                **txn_row_event(
                    txn,
                    "Copying",
                    _row_detail(
                        txn,
                        f"Writing row to {_sheet_label(sheet)} tab {sheet.tab_title()}",
                    ),
                ),
            )
            try:
                sheet.write_row(to_sheet_row(txn, settings, games=games))
            except Exception as exc:
                _emit(
                    on_event,
                    kind="log",
                    message=(
                        f"{_sheet_label(sheet)}: write failed for "
                        f"{txn.transaction_id} on tab {sheet.tab_title()}: {exc}"
                    ),
                )
                db.mark(txn.transaction_id, "failed", str(exc))
                result.failed += 1
                _emit(on_event, **txn_row_event(txn, "Failed", str(exc)))
                continue
            db.mark(txn.transaction_id, "copied", detail)
            result.copied += 1
            _emit(on_event, **txn_row_event(txn, "Copied", _row_detail(txn, detail)))
            _emit(
                on_event,
                kind="log",
                message=(
                    f"{_sheet_label(sheet)}: sent {txn.transaction_id} "
                    f"to tab {sheet.tab_title()}."
                ),
            )
        _blank_sheet_bank(sheet, on_event)
        return
    rows = [to_sheet_row(txn, settings, games=games) for txn in to_copy]
    for txn in to_copy:
        _emit(
            on_event,
            **txn_row_event(
                txn,
                "Copying",
                _row_detail(txn, f"Writing row to {_sheet_label(sheet)} tab {sheet.tab_title()}"),
            ),
        )
    try:
        sheet.write_rows(rows)
        start = getattr(sheet, "last_write_start", 0)
        skip_day = getattr(sheet, "_skip_day_column", False)
        _emit(
            on_event,
            kind="log",
            message=(
                f"{_sheet_label(sheet)}: wrote {len(to_copy)} row(s) "
                f"on tab {sheet.tab_title()}"
                + (f" starting at row {start}" if start else "")
                + (
                    " in B:L (skipped locked column A and rows 1–104)."
                    if skip_day
                    else "."
                )
            ),
        )
    except Exception as exc:
        _emit(
            on_event,
            kind="log",
            message=f"{_sheet_label(sheet)}: write failed on tab {sheet.tab_title()}: {exc}",
        )
        for txn in to_copy:
            db.mark(txn.transaction_id, "failed", str(exc))
            result.failed += 1
            _emit(on_event, **txn_row_event(txn, "Failed", str(exc)))
        return
    for txn in to_copy:
        db.mark(txn.transaction_id, "copied", detail)
        result.copied += 1
        _emit(on_event, **txn_row_event(txn, "Copied", _row_detail(txn, detail)))
    _blank_sheet_bank(sheet, on_event)


def _sync_one_day(
    settings: Settings,
    db: GatheringDB,
    sheet: SheetClient,
    day: str,
    txns: list[Transaction],
    result: PipelineResult,
    on_event: EventFn | None,
) -> None:
    try:
        sheet.use_day(day)
    except Exception as exc:
        _emit(
            on_event,
            kind="log",
            message=f"{_sheet_label(sheet)}: sync {day} failed: {exc}",
        )
        for txn in txns:
            db.mark(txn.transaction_id, "failed", str(exc))
            result.failed += 1
            _emit(on_event, **txn_row_event(txn, "Failed", str(exc)))
        return
    existing_ids, _by_date = sheet.id_index()
    missing = new_rows_only(txns, existing_ids)
    sheet_count = len(existing_ids)
    _emit(
        on_event,
        kind="sheet_tally",
        date=day,
        gui_count=len(txns),
        sheet_count=sheet_count,
        missing=len(missing),
    )
    _emit(
        on_event,
        kind="log",
        message=(
            f"{_sheet_label(sheet)}: sync {day} → tab {sheet.tab_title()}: "
            f"GUI {len(txns)} txn · this sheet {sheet_count} txn · missing {len(missing)}."
        ),
    )
    if not missing:
        _emit(
            on_event,
            kind="log",
            message=(
                f"{_sheet_label(sheet)}: tab {sheet.tab_title()} "
                f"already has every GUI record for {day}."
            ),
        )
        _blank_sheet_bank(sheet, on_event)
        return
    before = result.copied
    _write_day_rows(settings, db, sheet, day, missing, result, on_event, action="Restored")
    restored = result.copied - before
    still_missing = max(0, len(missing) - restored)
    _emit(
        on_event,
        kind="sheet_tally",
        date=day,
        gui_count=len(txns),
        sheet_count=sheet_count + restored,
        missing=still_missing,
    )
    _emit(
        on_event,
        kind="log",
        message=(
            f"{_sheet_label(sheet)}: restored {restored} missing row(s) for {day} "
            f"on tab {sheet.tab_title()}. "
            f"This sheet now {sheet_count + restored} txn · GUI {len(txns)} txn."
        ),
    )
