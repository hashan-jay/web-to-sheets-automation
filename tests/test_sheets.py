import unittest

from src.models import Transaction
from src.sheets import new_rows_only


class SheetDedupeTests(unittest.TestCase):
    def test_skips_ids_already_on_the_sheet(self) -> None:
        first = Transaction(transaction_id="17110853300", amount="30")
        again = Transaction(transaction_id="17110853300", amount="30")
        extra = Transaction(transaction_id="17110853301", amount="10")
        rows = new_rows_only([first, again, extra], {"17110853300"})
        self.assertEqual([row.transaction_id for row in rows], ["17110853301"])


if __name__ == "__main__":
    unittest.main()
