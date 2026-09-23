import unittest

from src.deposit_bank import (
    apply_deposit_sheet_bank,
    discover_bank_choices,
    extract_attachment_url,
    fill_deposit_banks,
    lookup_attachment_url,
    match_sheet_bank,
    normalize_deposit_sheet_bank,
    sheet_bank_choices,
    txn_attachment_url,
)
from src.mapper import sheet_bank, to_sheet_row
from src.models import Transaction
from tests.test_mapper import _settings


class DepositBankTests(unittest.TestCase):
    def test_fill_deposit_banks_skips_screenshot_ocr(self) -> None:
        txn = Transaction(transaction_id="1", status="DEPOSIT", amount="10")
        self.assertEqual(fill_deposit_banks(_settings(), [txn]), 0)
        self.assertFalse((txn.extras or {}).get("sheet_bank"))

    def test_gui_deposit_bank_fills_deposits_only(self) -> None:
        settings = _settings()
        settings.deposit_sheet_bank = "  Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS) "
        deposit = Transaction(transaction_id="1", status="STAFF DEPOSIT", amount="50")
        withdraw = Transaction(transaction_id="2", status="STAFF WITHDRAW", amount="20")
        self.assertEqual(normalize_deposit_sheet_bank(settings.deposit_sheet_bank), "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)")
        self.assertEqual(apply_deposit_sheet_bank(settings, [deposit, withdraw]), 1)
        self.assertEqual(
            deposit.extras.get("sheet_bank"),
            "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)",
        )
        self.assertFalse((withdraw.extras or {}).get("sheet_bank"))
        self.assertEqual(sheet_bank(deposit, settings), "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)")
        self.assertEqual(to_sheet_row(deposit, settings)[2], "Bank ANZ Plus LEANNE MARY HUMPHREYS (ANZ PLUS)")
        self.assertEqual(sheet_bank(withdraw, settings), "")
        self.assertEqual(to_sheet_row(withdraw, settings)[2], "")
        settings.deposit_sheet_bank = ""
        later = Transaction(transaction_id="3", status="DEPOSIT", amount="10")
        self.assertEqual(apply_deposit_sheet_bank(settings, [later]), 0)
        self.assertEqual(later.extras.get("sheet_bank"), "")
        self.assertEqual(to_sheet_row(later, settings)[2], "")

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
