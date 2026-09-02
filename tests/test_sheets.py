import unittest

from src.mapper import SHEET_COL_COUNT, pad_sheet_row
from src.models import Transaction
from src.errors import ConfigError
from src.sheets import (
    bank_clear_range,
    day_tab_candidates,
    index_sheet_ids,
    new_rows_only,
    office_file_error,
)


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

    def test_pad_sheet_row_keeps_staff_in_last_column(self) -> None:
        padded = pad_sheet_row(["30", "date", "bank"])
        self.assertEqual(len(padded), SHEET_COL_COUNT)
        self.assertEqual(padded[11], "")
        full = pad_sheet_row(["1", "2", "3", "4", "5", "6", "7", "brand", "", "player", "", "staff"])
        self.assertEqual(full[9], "player")
        self.assertEqual(full[11], "staff")

    def test_day_tab_candidates(self) -> None:
        self.assertEqual(day_tab_candidates("29"), ["29"])
        self.assertEqual(day_tab_candidates("9"), ["9", "09"])
        self.assertEqual(day_tab_candidates("09"), ["9", "09"])

    def test_office_file_error_explains_xlsx(self) -> None:
        mapped = office_file_error(
            Exception("APIError: [400]: This operation is not supported for this document. The document must not be an Office file.")
        )
        self.assertIsInstance(mapped, ConfigError)
        self.assertIn("Excel", str(mapped))
        self.assertIsNone(office_file_error(Exception("unrelated")))

    def test_bank_clear_range_skips_header(self) -> None:
        start, end = bank_clear_range(["ID", "17110853300", "17110853301"])
        self.assertEqual((start, end), (2, 3))


if __name__ == "__main__":
    unittest.main()
