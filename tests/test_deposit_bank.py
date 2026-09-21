import unittest

from src.deposit_bank import (
    discover_bank_choices,
    extract_attachment_url,
    lookup_attachment_url,
    match_sheet_bank,
    sheet_bank_choices,
    txn_attachment_url,
)
from src.mapper import sheet_bank, to_sheet_row
from src.models import Transaction
from tests.test_mapper import _settings


class DepositBankTests(unittest.TestCase):
    def test_match_sheet_bank_from_screenshot_text(self) -> None:
        choices = ("ANZPLUS O'NEILL R W", "CBA SMITH J")
        self.assertEqual(
            match_sheet_bank("Paid to ANZPLUS account O'NEILL R W", choices),
            "ANZPLUS O'NEILL R W",
        )
        self.assertEqual(match_sheet_bank("CBA transfer SMITH J 12.00", choices), "CBA SMITH J")
        self.assertEqual(match_sheet_bank("random phone screenshot", choices), "")

    def test_extract_attachment_url(self) -> None:
        self.assertEqual(
            extract_attachment_url(
                {"receipt": "https://cdn.example.com/proofs/a.jpg"},
                "https://site.example",
            ),
            "https://cdn.example.com/proofs/a.jpg",
        )
        self.assertEqual(
            extract_attachment_url("/uploads/slip.png", "https://site.example"),
            "https://site.example/uploads/slip.png",
        )
        self.assertEqual(extract_attachment_url("ATTACHMENT"), "")

    def test_withdraw_bank_stays_blank(self) -> None:
        settings = _settings()
        withdraw = Transaction(
            transaction_id="9",
            status="WITHDRAW",
            extras={"sheet_bank": "ANZPLUS O'NEILL R W"},
        )
        self.assertEqual(sheet_bank(withdraw, settings), "")
        self.assertEqual(to_sheet_row(withdraw, settings)[2], "")
        staff_withdraw = Transaction(
            transaction_id="10",
            status="STAFF WITHDRAW",
            extras={"sheet_bank": "ANZPLUS O'NEILL R W"},
        )
        self.assertEqual(sheet_bank(staff_withdraw, settings), "")
        self.assertEqual(to_sheet_row(staff_withdraw, settings)[2], "")
        self.assertEqual(to_sheet_row(staff_withdraw, settings)[5], "Withdraw")

    def test_sheet_bank_choices_include_default(self) -> None:
        settings = _settings()
        settings.bank_accounts = ("CBA SMITH J",)
        self.assertEqual(
            sheet_bank_choices(settings),
            ("ANZPLUS O'NEILL R W", "CBA SMITH J"),
        )

    def test_txn_attachment_url_reads_model_field(self) -> None:
        txn = Transaction(
            transaction_id="1",
            status="DEPOSIT",
            attachment="/files/proof.jpg",
        )
        self.assertEqual(
            txn_attachment_url(txn, "https://site.example"),
            "https://site.example/files/proof.jpg",
        )

    def test_discover_bank_choices_reads_banks_sheet(self) -> None:
        class FakeWs:
            title = "10"

            def col_values(self, _index):
                return ["BANK", "ANZPLUS O'NEILL R W"]

        class FakeBanks:
            title = "Banks"

            def get_all_values(self):
                return [["BANK"], ["Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)"]]

        class FakeSpreadsheet:
            def worksheets(self):
                return [FakeWs(), FakeBanks()]

            def fetch_sheet_metadata(self, _params):
                return {}

        class FakeSheet:
            ws = FakeWs()
            spreadsheet = FakeSpreadsheet()

        sheet = FakeSheet()
        self.assertIn(
            "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
            discover_bank_choices(sheet),
        )
        self.assertEqual(discover_bank_choices(sheet), sheet._bank_choices)

    def test_lookup_attachment_url_keeps_existing(self) -> None:
        txn = Transaction(
            transaction_id="1",
            status="DEPOSIT",
            extras={"attachment": "https://cdn.example.com/a.jpg"},
        )
        self.assertEqual(
            lookup_attachment_url(txn, None, "https://site.example"),
            "https://cdn.example.com/a.jpg",
        )


if __name__ == "__main__":
    unittest.main()
