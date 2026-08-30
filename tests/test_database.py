import tempfile
import unittest
from pathlib import Path

from src.database import GatheringDB
from src.models import Transaction


class GatheringDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = GatheringDB(Path(self.tmp.name) / "gathering.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ingest_and_pending(self) -> None:
        first = Transaction(transaction_id="17110853300", amount="30", name="Caleb")
        added = self.db.ingest([first, first])
        self.assertEqual(added, 1)
        pending = self.db.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].transaction_id, "17110853300")
        self.db.mark("17110853300", "copied", "ok")
        self.assertEqual(self.db.pending(), [])
        self.assertEqual(self.db.counts()["copied"], 1)
        self.assertEqual(len(self.db.all_records()), 1)

    def test_reset_failed_to_pending(self) -> None:
        txn = Transaction(transaction_id="17110853301", amount="45", status="DEPOSIT")
        self.db.ingest([txn])
        self.db.mark("17110853301", "failed", "429")
        self.assertEqual(self.db.reset_to_pending(["17110853301"]), 1)
        self.assertEqual(self.db.counts()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
