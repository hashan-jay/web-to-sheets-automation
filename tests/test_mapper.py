import unittest
from pathlib import Path

from src.config import Settings
from src.mapper import (
    SHEET_COL_COMPANY,
    SHEET_COL_COUNT,
    SHEET_COL_PLAYER,
    SHEET_COL_STAFF,
    clean_name,
    day_from_datetime,
    normalize_brand,
    normalize_status,
    record_local_datetime,
    sheet_amount,
    sheet_status,
    sheet_tab_name,
    to_sheet_row,
    txn_local_date,
)
from src.models import Transaction


def _settings() -> Settings:
    return Settings(
        dashboard_url="https://example.com",
        dashboard_username="",
        dashboard_password="",
        dashboard_2fa="",
        manual_login_seconds=0,
        filter_date_from="",
        filter_date_to="",
        filter_type="ACTIVE",
        filter_status="ANY",
        google_sheet_id="",
        google_worksheet="",
        google_credentials_path=Path("credentials/service-account.json"),
        default_bank_account="ANZPLUS O'NEILL R W",
        default_brand="FUCKSPIN",
        default_staff_code="SL0017",
        brand_aliases={"FUCKSPINVIPA": "FUCKSPIN"},
    )


class MapperTests(unittest.TestCase):
    def test_clean_name_strips_tag(self) -> None:
        self.assertEqual(
            clean_name("[JKFCKSPNAU] Caleb William Needham"),
            "Caleb William Needham",
        )

    def test_sheet_tab_name_uses_day_number(self) -> None:
        self.assertEqual(sheet_tab_name("2026-08-29"), "29")
        self.assertEqual(sheet_tab_name("2026-08-09"), "9")
        self.assertEqual(sheet_tab_name("29"), "29")
        self.assertEqual(sheet_tab_name("09"), "9")
        self.assertEqual(sheet_tab_name(""), "")

    def test_day_and_status(self) -> None:
        self.assertEqual(day_from_datetime("2026-08-28 23:30"), "28")
        self.assertEqual(normalize_status("DEPOSIT"), "Deposit")
        self.assertEqual(
            record_local_datetime("", "2026-08-29 10:44", "2026-08-29 10:45"),
            "2026-08-29 10:44",
        )
        self.assertEqual(
            record_local_datetime("2026-08-30 15:04:01", "2026-08-29 10:44"),
            "2026-08-30 15:04:01",
        )
        self.assertEqual(
            txn_local_date(Transaction(transaction_id="1", created="2026-08-30 15:04")),
            "2026-08-30",
        )
        self.assertEqual(
            txn_local_date(
                Transaction(
                    transaction_id="2",
                    datetime="2026-08-29 23:50",
                    extras={"tally_date": "2026-08-30"},
                )
            ),
            "2026-08-30",
        )

    def test_sheet_row_mapping(self) -> None:
        settings = _settings()
        txn = Transaction(
            transaction_id="17110853300",
            username="A10603620",
            name="[JKFCKSPNAU] Caleb William Needham",
            bank_account_name="Caleb William Needham",
            amount="30",
            datetime="2026-08-28 23:30",
            status="DEPOSIT",
            created="2026-08-28 19:16",
            brand="FUCKSPINVIPA",
        )
        self.assertEqual(
            to_sheet_row(txn, settings),
            [
                "28",
                "2026-08-28 23:30",
                "ANZPLUS O'NEILL R W",
                "Caleb William Needham",
                "30",
                "Deposit",
                "17110853300",
                "FUCKSPIN",
                "",
                "A10603620",
                "",
                "",
            ],
        )
        self.assertEqual(sheet_status("DEPOSIT"), "Deposit")
        self.assertEqual(sheet_amount("30", "DEPOSIT"), "30")
        self.assertEqual(normalize_brand("", settings), "")
        self.assertEqual(normalize_brand("FUCKSPINVIPC", settings), "FUCKSPIN")
        self.assertEqual(normalize_brand("NETLOSSN", settings), "")
        self.assertEqual(normalize_brand("POKIESPARK VIP", settings), "POKIESPARK")

    def test_withdraw_card_mapping(self) -> None:
        settings = _settings()
        txn = Transaction(
            transaction_id="17113600239",
            username="A51088178",
            name="[JKFCKSPNAU] Timothy David Evans",
            bank_account_name="Timothy David Evans",
            amount="530.27",
            datetime="2026-08-28 23:30",
            bank="PIPN",
            method="Manual",
            status="WITHDRAW",
            brand="FUCKSPINVIPA",
            bsb="815000",
            pay_id="61414769587",
            bank_lock="1",
        )
        row = to_sheet_row(txn, settings)
        self.assertEqual(
            row,
            [
                "28",
                "2026-08-28 23:30",
                "",
                "Timothy David Evans",
                "-530.27",
                "Withdraw",
                "17113600239",
                "FUCKSPIN",
                "",
                "A51088178",
                "",
                "",
            ],
        )
        deposit = to_sheet_row(
            Transaction(
                transaction_id="1",
                username="A1",
                amount="10",
                status="DEPOSIT",
                brand="FUCKSPIN",
            ),
            settings,
        )
        self.assertEqual(len(row), SHEET_COL_COUNT)
        self.assertEqual(len(deposit), SHEET_COL_COUNT)
        self.assertEqual(row[SHEET_COL_COMPANY], deposit[SHEET_COL_COMPANY])
        self.assertEqual(row[SHEET_COL_STAFF], deposit[SHEET_COL_STAFF])
        self.assertEqual(row[SHEET_COL_PLAYER], "A51088178")
        self.assertEqual(deposit[SHEET_COL_PLAYER], "A1")
        self.assertEqual(row[2], "")
        self.assertEqual(deposit[2], "ANZPLUS O'NEILL R W")
        self.assertEqual(sheet_status("WITHDRAWAL"), "Withdraw")
        self.assertEqual(sheet_amount("50", "WITHDRAW"), "-50")
        self.assertEqual(sheet_amount("-50", "WITHDRAWAL"), "-50")
        self.assertEqual(normalize_status("WITHDRAW"), "Withdraw")


if __name__ == "__main__":
    unittest.main()
