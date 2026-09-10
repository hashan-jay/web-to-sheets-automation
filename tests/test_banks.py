import unittest

from src.banks import (
    format_bank_label,
    parse_website_banks,
    seed_bank_dropdown,
)
from src.config import _csv_tuple
from src.deposit_bank import match_sheet_bank


class FakeWorksheet:
    def __init__(self, title: str, sheet_id: int) -> None:
        self.title = title
        self.id = sheet_id
        self.row_count = 80
        self.values: list[list[str]] = []

    def clear(self) -> None:
        self.values = []

    def update(self, range_name, values, value_input_option=None) -> None:
        self.values = list(values)


class FakeSpreadsheet:
    def __init__(self) -> None:
        self.sheets = [FakeWorksheet("10", 10)]
        self.requests = None

    def worksheet(self, title: str):
        for sheet in self.sheets:
            if sheet.title == title:
                return sheet
        raise Exception("Worksheet not found")

    def add_worksheet(self, title: str, rows: int, cols: int):
        sheet = FakeWorksheet(title, 99)
        self.sheets.append(sheet)
        return sheet

    def worksheets(self):
        return list(self.sheets)

    def batch_update(self, body):
        self.requests = body


class FakeSheet:
    def __init__(self) -> None:
        self.spreadsheet = FakeSpreadsheet()


class BanksTests(unittest.TestCase):
    def test_format_bank_label_joins_bank_and_account(self) -> None:
        self.assertEqual(
            format_bank_label("Bank ANZ Plus", "LEANNE MARY HUMPHREYS (ANZ PLUS)"),
            "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
        )
        self.assertEqual(
            format_bank_label("", "LEANNE MARY HUMPHREYS (ANZ PLUS)"),
            "LEANNE MARY HUMPHREYS (ANZ PLUS)",
        )
        self.assertEqual(
            format_bank_label("National Australia Bank", "National Australia Bank A N Seymore"),
            "National Australia Bank A N Seymore",
        )

    def test_parse_website_banks_from_api_rows(self) -> None:
        banks = parse_website_banks(
            {
                "data": {
                    "banks": [
                        {
                            "bankName": "Bank ANZ Plus",
                            "accountName": "LEANNE MARY HUMPHREYS (ANZ PLUS)",
                            "accountNumber": "44599414717",
                            "id": "16720425833",
                        },
                        {
                            "bank_name": "National Australia Bank",
                            "account_name": "ALBERT KEITH AH SEE (NABBER)",
                        },
                    ]
                }
            }
        )
        self.assertEqual(
            [bank.label for bank in banks],
            [
                "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
                "National Australia Bank ALBERT KEITH AH SEE (NABBER)",
            ],
        )

    def test_seed_bank_dropdown_writes_banks_sheet(self) -> None:
        sheet = FakeSheet()
        count = seed_bank_dropdown(
            sheet,
            (
                "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
                "National Australia Bank ALBERT KEITH AH SEE (NABBER)",
            ),
        )
        self.assertEqual(count, 2)
        banks_ws = next(item for item in sheet.spreadsheet.sheets if item.title == "Banks")
        self.assertEqual(banks_ws.values[0], ["BANK"])
        self.assertEqual(
            banks_ws.values[1],
            ["Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)"],
        )
        self.assertTrue(sheet.spreadsheet.requests)
        request = sheet.spreadsheet.requests["requests"][0]["setDataValidation"]
        self.assertEqual(request["range"]["sheetId"], 10)
        self.assertEqual(request["range"]["startColumnIndex"], 2)
        self.assertEqual(request["rule"]["condition"]["type"], "ONE_OF_RANGE")

    def test_csv_tuple_prefers_pipe_separator(self) -> None:
        self.assertEqual(
            _csv_tuple("ANZPLUS O'NEILL R W,CBA SMITH J"),
            ("ANZPLUS O'NEILL R W", "CBA SMITH J"),
        )
        self.assertEqual(
            _csv_tuple("Bank ANZ Plus LEANNE|National Australia Bank ALBERT"),
            ("Bank ANZ Plus LEANNE", "National Australia Bank ALBERT"),
        )

    def test_match_prefers_account_name_from_screenshot(self) -> None:
        choices = (
            "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
            "Bank ANZ Plus SALLY CHONTELLE GAUT (ANZ PLUS)",
        )
        self.assertEqual(
            match_sheet_bank("Receipt LEANNE HUMPHREYS ANZ PLUS 120.00", choices),
            "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
        )
        self.assertEqual(match_sheet_bank("Paid via ANZ PLUS only", choices), "")


if __name__ == "__main__":
    unittest.main()
