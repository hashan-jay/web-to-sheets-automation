import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database import GatheringDB
from src.models import Transaction
from src.pipeline import copy_pending_to_sheet, gather_from_dashboard, run_pipeline, unsent_candidates
from src.scraper import ScrapeCapture
from tests.test_dashboard_api import _settings


class GatherEventsTests(unittest.TestCase):
    def test_gather_emits_gathered_only_for_new_ids(self) -> None:
        events: list[dict] = []
        txn = Transaction(
            transaction_id="17113600239",
            username="A1",
            amount="10",
            status="DEPOSIT",
        )
        capture = ScrapeCapture(
            transactions=[txn],
            website_records=1,
            website_total="10.00",
            filter_date="2026-09-08",
            filter_status="COMPLETED",
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(database_path=Path(folder) / "gathering.db")
            db = GatheringDB(settings.database_path)
            with patch("src.pipeline.scrape_transactions", return_value=capture):
                first = gather_from_dashboard(settings, db, on_event=events.append)
                second_events: list[dict] = []
                gather_from_dashboard(settings, db, on_event=second_events.append)
        gathered_first = [event for event in events if event.get("status") == "Gathered"]
        gathered_second = [
            event for event in second_events if event.get("status") == "Gathered"
        ]
        self.assertEqual(first.scraped, 1)
        self.assertEqual(len(gathered_first), 1)
        self.assertEqual(gathered_second, [])
        self.assertTrue(
            any("already in the GUI" in str(event.get("message") or "") for event in second_events)
        )

    def test_gather_restores_failed_ids_to_the_gui(self) -> None:
        events: list[dict] = []
        txn = Transaction(
            transaction_id="17113600240",
            username="A2",
            amount="15",
            status="STAFF DEPOSIT",
        )
        capture = ScrapeCapture(
            transactions=[txn],
            website_records=1,
            website_total="15.00",
            filter_date="2026-09-21",
            filter_status="COMPLETED",
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(database_path=Path(folder) / "gathering.db")
            db = GatheringDB(settings.database_path)
            db.ingest([txn])
            db.mark("17113600240", "failed", "sheet not shared")
            with patch("src.pipeline.scrape_transactions", return_value=capture):
                gather_from_dashboard(settings, db, on_event=events.append)
            self.assertEqual(db.by_status("pending")[0].transaction_id, "17113600240")
        gathered = [event for event in events if event.get("status") == "Gathered"]
        self.assertEqual(len(gathered), 1)
        self.assertIn("Restored", gathered[0].get("detail") or "")

    def test_gather_maps_pending_tag_to_sheet_brand(self) -> None:
        events: list[dict] = []
        txn = Transaction(
            transaction_id="17120001001",
            username="A9",
            amount="50",
            status="STAFF DEPOSIT",
            tags=["PENDING", "CUNTWIN"],
        )
        capture = ScrapeCapture(
            transactions=[txn],
            website_records=1,
            website_total="50.00",
            filter_date="2026-09-23",
            filter_status="COMPLETED",
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(
                database_path=Path(folder) / "gathering.db",
                sheet_brands=("CUNTWIN", "COKESPIN", "MATE29"),
            )
            db = GatheringDB(settings.database_path)
            with patch("src.pipeline.scrape_transactions", return_value=capture):
                gather_from_dashboard(settings, db, on_event=events.append)
            stored = db.pending()
        gathered = [event for event in events if event.get("status") == "Gathered"]
        self.assertEqual(gathered[0].get("brand"), "CUNTWIN")
        self.assertEqual(stored[0].brand, "CUNTWIN")
        self.assertIn("CUNTWIN", stored[0].tags)


class UnsentCandidateTests(unittest.TestCase):
    def test_finds_copied_record_that_is_still_to_send(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            db = GatheringDB(Path(folder) / "gathering.db")
            sent = Transaction(
                transaction_id="17110000001",
                amount="10",
                datetime="2026-09-10 09:00",
                status="DEPOSIT",
            )
            missed = Transaction(
                transaction_id="17110000002",
                amount="20",
                datetime="2026-09-10 10:00",
                status="DEPOSIT",
            )
            other_day = Transaction(
                transaction_id="17110000003",
                amount="30",
                datetime="2026-09-09 10:00",
                status="DEPOSIT",
            )
            db.ingest([sent, missed, other_day])
            db.mark("17110000001", "copied", "ok")
            db.mark("17110000002", "copied", "marked sent but not on sheet")
            db.mark("17110000003", "copied", "other day already sent")
            found = unsent_candidates(db, day="2026-09-10")
            self.assertEqual(
                {txn.transaction_id for txn in found},
                {"17110000001", "17110000002"},
            )
            only_missed = unsent_candidates(db, only_ids={"17110000002"})
            self.assertEqual([txn.transaction_id for txn in only_missed], ["17110000002"])


class SendStaffRowsTests(unittest.TestCase):
    def test_send_writes_staff_deposit_and_withdraw_separately(self) -> None:
        writes: list[tuple[bool | None, list[str]]] = []

        class FakeSheet:
            slot = 1
            sheet_id = "sheet-1"
            last_write_start = 105

            def __init__(self) -> None:
                self.spreadsheet = type("S", (), {"title": "GROUP U AUD SEPTEMBER 2026"})()

            def use_day(self, day: str):
                self.day = day
                return self

            def tab_title(self) -> str:
                return "21"

            def existing_ids(self) -> set[str]:
                return set()

            def write_rows(self, rows, *, withdraw=None):
                writes.append((withdraw, [row[6] for row in rows]))
                self.last_write_start = 1024 if withdraw else 105
                return len(rows)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(
                database_path=Path(folder) / "gathering.db",
                google_sheet_id="1GbnitoV4c_C25UU2JHBC-ICe4KHofTBlrX4HrutwubI",
                filter_date_from="2026-09-21",
            )
            db = GatheringDB(settings.database_path)
            db.ingest(
                [
                    Transaction(
                        transaction_id="17120000010",
                        amount="40",
                        datetime="2026-09-21 09:00",
                        status="STAFF DEPOSIT",
                    ),
                    Transaction(
                        transaction_id="17120000011",
                        amount="15",
                        datetime="2026-09-21 10:00",
                        status="STAFF WITHDRAW",
                    ),
                ]
            )
            with (
                patch("src.pipeline._open_sheet", return_value=FakeSheet()),
                patch("src.pipeline.fill_deposit_banks", return_value=0),
                patch("src.config.Settings.require_sheets", return_value=None),
            ):
                result = copy_pending_to_sheet(settings, db)
        self.assertEqual(result.copied, 2)
        self.assertEqual(result.failed, 0)
        self.assertEqual(
            writes,
            [
                (False, ["17120000010"]),
                (True, ["17120000011"]),
            ],
        )

    def test_send_keeps_scraped_rows_when_sheet_cannot_open(self) -> None:
        events: list[dict] = []
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(
                database_path=Path(folder) / "gathering.db",
                google_sheet_id="1lpVFyp1c7mFw9iwttY4LF8mXRJBeA2thLZcNFjpH3_E",
                filter_date_from="2026-09-21",
            )
            db = GatheringDB(settings.database_path)
            db.ingest(
                [
                    Transaction(
                        transaction_id="17120000020",
                        amount="12",
                        datetime="2026-09-21 11:00",
                        status="STAFF DEPOSIT",
                    )
                ]
            )
            with (
                patch(
                    "src.pipeline._open_sheet",
                    side_effect=PermissionError("The caller does not have permission"),
                ),
                patch("src.config.Settings.require_sheets", return_value=None),
            ):
                result = copy_pending_to_sheet(settings, db, on_event=events.append)
        self.assertEqual(result.copied, 0)
        self.assertEqual(result.failed, 0)
        self.assertEqual(db.by_status("pending")[0].transaction_id, "17120000020")
        self.assertFalse(any(event.get("status") == "Failed" for event in events))
        self.assertTrue(any("could not open" in str(item.get("message") or "") for item in events))


class AutomatedRunScrapeTests(unittest.TestCase):
    def test_pipeline_keeps_scrape_when_sheet_write_raises(self) -> None:
        events: list[dict] = []
        txn = Transaction(
            transaction_id="17120000030",
            username="A3",
            amount="18",
            status="STAFF DEPOSIT",
            datetime="2026-09-21 12:00",
        )
        capture = ScrapeCapture(
            transactions=[txn],
            website_records=1,
            website_total="18.00",
            filter_date="2026-09-21",
            filter_status="COMPLETED",
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(
                database_path=Path(folder) / "gathering.db",
                google_sheet_id="1lpVFyp1c7mFw9iwttY4LF8mXRJBeA2thLZcNFjpH3_E",
                filter_date_from="2026-09-21",
            )
            with (
                patch("src.pipeline.scrape_transactions", return_value=capture),
                patch(
                    "src.pipeline.copy_pending_to_sheet",
                    side_effect=PermissionError("The caller does not have permission"),
                ),
            ):
                result = run_pipeline(
                    settings,
                    on_event=events.append,
                    scrape=True,
                    write_sheet=True,
                )
            db = GatheringDB(settings.database_path)
            self.assertEqual(result.scraped, 1)
            self.assertEqual(db.pending()[0].transaction_id, "17120000030")
        self.assertTrue(any(event.get("status") == "Gathered" for event in events))
        self.assertTrue(
            any("Scrape finished" in str(event.get("message") or "") for event in events)
        )


if __name__ == "__main__":
    unittest.main()
