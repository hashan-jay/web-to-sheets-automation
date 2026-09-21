import unittest

from src.mapper import SHEET_COL_COUNT, pad_sheet_row
from src.models import Transaction
from src.errors import ConfigError
from src.sheets import (
    LEDGER_FIRST_DATA_ROW,
    WITHDRAW_FIRST_DATA_ROW,
    bank_clear_range,
    day_tab_candidates,
    find_day_worksheet,
    index_sheet_ids,
    sheets_retry_wait,
    ledger_skip_columns,
    ledger_write_batches,
    ledger_write_plan,
    locked_columns_in_rows,
    looks_like_ledger_tab,
    new_rows_only,
    next_append_row,
    next_unlocked_row,
    office_file_error,
    parse_locked_blocks,
    protected_range_error,
    row_is_withdraw,
    sheet_open_error,
    uses_ledger_start,
    uses_locked_day_column,
    writable_append_row,
)


class SheetDedupeTests(unittest.TestCase):
    def test_skips_ids_already_on_the_sheet(self) -> None:
        first = Transaction(transaction_id="17110853300", amount="30")
        again = Transaction(transaction_id="17110853300", amount="30")
        extra = Transaction(transaction_id="17110853301", amount="10")
        rows = new_rows_only([first, again, extra], {"17110853300"})
        self.assertEqual([row.transaction_id for row in rows], ["17110853301"])
        scientific = new_rows_only(
            [Transaction(transaction_id="17110853300")],
            {"1.71108533E10"},
        )
        self.assertEqual(scientific, [])

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

    def test_sheets_retry_wait_is_short(self) -> None:
        self.assertEqual(sheets_retry_wait(0), 2.0)
        self.assertEqual(sheets_retry_wait(1), 4.0)
        self.assertEqual(sheets_retry_wait(2), 8.0)
        self.assertEqual(sheets_retry_wait(9), 8.0)

    def test_find_day_worksheet_uses_provided_list(self) -> None:
        class Fake:
            def __init__(self, title: str) -> None:
                self.title = title

            def worksheets(self):
                raise AssertionError("should use the cached worksheet list")

        found = find_day_worksheet(Fake("x"), "21", [Fake("21")])
        self.assertEqual(found.title, "21")

    def test_office_file_error_explains_xlsx(self) -> None:
        mapped = office_file_error(
            Exception("APIError: [400]: This operation is not supported for this document. The document must not be an Office file.")
        )
        self.assertIsInstance(mapped, ConfigError)
        self.assertIn("Excel", str(mapped))
        self.assertIsNone(office_file_error(Exception("unrelated")))
        locked = protected_range_error(
            Exception("APIError: [400]: You are trying to edit a protected cell or object.")
        )
        self.assertIsInstance(locked, ConfigError)
        self.assertIn("Protect sheets and ranges", str(locked))
        self.assertIn("row 105", str(locked))
        self.assertIsNone(protected_range_error(Exception("unrelated")))
        denied = sheet_open_error(PermissionError())
        self.assertIsInstance(denied, ConfigError)
        self.assertIn("not shared", str(denied))
        self.assertIn("sheets-writer@", str(denied))
        denied_api = sheet_open_error(Exception("APIError: [403]: The caller does not have permission"))
        self.assertIsInstance(denied_api, ConfigError)
        self.assertIsNone(sheet_open_error(Exception("unrelated")))

    def test_september_ledger_starts_at_row_105(self) -> None:
        self.assertTrue(uses_ledger_start("GROUP U AUD SEPTEMBER 2026"))
        self.assertTrue(uses_ledger_start("Copy of GROUP D AUD SEPTEMBER 2026"))
        self.assertTrue(uses_ledger_start("GROUP K AUD SEPTEMBER 2026"))
        self.assertTrue(uses_ledger_start("Copy of GROUP W AUD SEPTEMBER 2026"))
        self.assertTrue(uses_ledger_start("KABOOM Test AUD SEPTEMBER 2026"))
        self.assertTrue(uses_locked_day_column("KABOOM Test AUD SEPTEMBER 2026"))
        self.assertFalse(uses_ledger_start("GROUP N DUMMY"))
        self.assertFalse(uses_ledger_start("GROUP D"))
        self.assertEqual(
            next_append_row(["ID"] + [""] * 110, first_data_row=LEDGER_FIRST_DATA_ROW),
            105,
        )
        ids = [""] * 103 + ["ID", "17110853300", "17110853301"]
        self.assertEqual(next_append_row(ids, first_data_row=105), 107)
        junk_at_bottom = [""] * 103 + ["ID"] + [""] * 50 + ["17110853300"]
        self.assertEqual(next_append_row(junk_at_bottom, first_data_row=105), 105)
        self.assertEqual(next_append_row(["ID", "17110853300"], 1), 3)
        self.assertTrue(uses_locked_day_column("Copy of GROUP D AUD SEPTEMBER 2026"))
        self.assertTrue(uses_locked_day_column("GROUP U AUD SEPTEMBER 2026"))
        self.assertTrue(uses_locked_day_column("GROUP K AUD SEPTEMBER 2026"))
        self.assertFalse(uses_locked_day_column("GROUP N DUMMY"))
        day_col = [""] * 103 + ["DAY"]
        id_col = [""] * 103 + ["ID"]
        self.assertTrue(looks_like_ledger_tab(day_col, id_col))
        self.assertFalse(looks_like_ledger_tab(["DAY"], ["ID"]))
        range_name, values, start = ledger_write_plan(
            [["2", "2026-09-02", "ANZ", "Name", "10", "Deposit", "1", "FUCKFUCK", "", "A1", "", ""]],
            start=20,
            skip_day_column=True,
            first_data_row=105,
            skip_bank_column=True,
        )
        self.assertEqual(start, 105)
        self.assertEqual(range_name, "B105:B105")
        self.assertEqual(values[0][0], "2026-09-02")
        self.assertNotIn("2", values[0][:1])
        skip_batches, skip_start = ledger_write_batches(
            [["2", "2026-09-02", "ANZ", "Name", "10", "Deposit", "1", "FUCKFUCK", "", "A1", "", ""]],
            start=20,
            skip_columns=ledger_skip_columns(True, True),
            first_data_row=105,
        )
        self.assertEqual(skip_start, 105)
        self.assertEqual([item[0] for item in skip_batches], ["B105:B105", "D105:L105"])
        kept_range, kept_values, kept_start = ledger_write_plan(
            [["10", "2026-09-10", "ANZPLUS O'NEILL R W", "Name", "10", "Deposit", "9", "FUCKSPIN", "", "A1", "", ""]],
            start=105,
            skip_day_column=True,
            skip_bank_column=False,
        )
        self.assertEqual(kept_start, 105)
        self.assertEqual(kept_range, "B105:L105")
        self.assertEqual(kept_values[0][1], "ANZPLUS O'NEILL R W")
        dummy_range, dummy_values, dummy_start = ledger_write_plan(
            [["2", "2026-09-02", "", "Name", "10", "Deposit", "1", "FUCKSPIN", "", "A1", "", ""]],
            start=2,
            skip_day_column=False,
        )
        self.assertEqual(dummy_start, 2)
        self.assertEqual(dummy_range, "A2:L2")
        self.assertEqual(dummy_values[0][0], "2")
        skip = ledger_skip_columns(True, True)
        batches, batch_start = ledger_write_batches(
            [["10", "2026-09-10", "ANZ", "Name", "10", "Deposit", "9", "FUCKSPIN", "", "A1", "", ""]],
            start=20,
            skip_columns=skip,
            first_data_row=105,
        )
        self.assertEqual(batch_start, 105)
        self.assertEqual([item[0] for item in batches], ["B105:B105", "D105:L105"])
        self.assertEqual(batches[0][1][0], ["2026-09-10"])
        self.assertEqual(batches[1][1][0][0], "Name")
        self.assertEqual(batches[1][1][0][3], "9")

    def test_withdrawals_start_at_row_1024(self) -> None:
        self.assertEqual(WITHDRAW_FIRST_DATA_ROW, 1024)
        self.assertEqual(
            next_append_row(["ID"] + [""] * 1100, first_data_row=WITHDRAW_FIRST_DATA_ROW),
            1024,
        )
        ids = [""] * 1023 + ["17110000001", "17110000002"]
        self.assertEqual(next_append_row(ids, first_data_row=1024), 1026)
        deposit_ids = [""] * 104 + ["17110000001"] + [""] * 20
        self.assertEqual(
            next_append_row(deposit_ids, first_data_row=105, last_data_row=1023),
            106,
        )
        full = [""] * 104 + ["1"] * 919
        self.assertEqual(next_append_row(full, first_data_row=105, last_data_row=1023), 0)
        range_name, values, start = ledger_write_plan(
            [["9", "2026-09-09", "", "Name", "-10", "Withdraw", "2", "FUCKSPIN", "", "A1", "", ""]],
            start=20,
            skip_day_column=False,
            first_data_row=1024,
        )
        self.assertEqual(start, 1024)
        self.assertEqual(range_name, "A1024:L1024")
        self.assertTrue(row_is_withdraw(values[0]))
        self.assertTrue(
            row_is_withdraw(["9", "2026-09-09", "", "Name", "-10", "Withdraw", "2"])
        )
        self.assertFalse(
            row_is_withdraw(["9", "2026-09-09", "", "Name", "10", "Deposit", "1"])
        )

    def test_bank_clear_range_skips_header(self) -> None:
        start, end = bank_clear_range(["ID", "17110853300", "17110853301"])
        self.assertEqual((start, end), (2, 3))

    def test_skips_protected_cells_and_writes_after_the_lock(self) -> None:
        metadata = {
            "sheets": [
                {
                    "properties": {"sheetId": 10, "title": "10"},
                    "protectedRanges": [
                        {
                            "range": {
                                "sheetId": 10,
                                "startRowIndex": 0,
                                "endRowIndex": 1044,
                                "startColumnIndex": 2,
                                "endColumnIndex": 43,
                            },
                            "editors": {"users": ["owner@example.com"]},
                        },
                        {
                            "range": {
                                "sheetId": 10,
                                "startColumnIndex": 0,
                                "endColumnIndex": 1,
                            },
                            "editors": {"users": ["owner@example.com"]},
                        },
                    ],
                }
            ]
        }
        blocks = parse_locked_blocks(
            metadata, 10, "sheets-writer@finance-automation-507106.iam.gserviceaccount.com"
        )
        self.assertEqual(locked_columns_in_rows(blocks, 105, 105) & {0, 2, 3, 6}, {0, 2, 3, 6})
        self.assertEqual(next_unlocked_row(blocks, 105, last_row=1023), 0)
        self.assertEqual(
            writable_append_row([""] * 110, blocks, first_data_row=105, last_data_row=1023),
            105,
        )
        writer_blocks = parse_locked_blocks(
            metadata, 10, "owner@example.com"
        )
        self.assertEqual(writer_blocks, [])
        self.assertEqual(next_unlocked_row(writer_blocks, 105, last_row=1023), 105)


if __name__ == "__main__":
    unittest.main()
