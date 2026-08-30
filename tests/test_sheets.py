import unittest

from src.models import Transaction
from src.sheets import index_sheet_ids, new_rows_only


class SheetDedupeTests(unittest.TestCase):
    def test_skips_ids_already_on_the_sheet(self) -> None:
        first = Transaction(transaction_id="17110853300", amount="30")
        again = Transaction(transaction_id="17110853300", amount="30")
        extra = Transaction(transaction_id="17110853301", amount="10")
        rows = new_rows_only([first, again, extra], {"17110853300"})
        self.assertEqual([row.transaction_id for row in rows], ["17110853301"])

    def test_index_sheet_ids_groups_by_date(self) -> None:
        all_ids, by_date = index_sheet_ids(
            ["Datetime", "2026-08-30 10:44", "2026-08-29 09:00", "2026-08-30 11:00"],
            ["ID", "17110853300", "17110853301", "17110853302"],
        )
        self.assertEqual(all_ids, {"17110853300", "17110853301", "17110853302"})
        self.assertEqual(by_date["2026-08-30"], {"17110853300", "17110853302"})
        self.assertEqual(by_date["2026-08-29"], {"17110853301"})


if __name__ == "__main__":
    unittest.main()
