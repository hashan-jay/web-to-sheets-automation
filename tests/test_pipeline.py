import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database import GatheringDB
from src.models import Transaction
from src.pipeline import gather_from_dashboard
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


if __name__ == "__main__":
    unittest.main()
