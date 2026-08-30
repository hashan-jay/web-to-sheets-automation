import unittest
from pathlib import Path

from src.config import Settings
from src.mapper import (
    clean_name,
    day_from_datetime,
    normalize_brand,
    normalize_status,
    record_local_datetime,
    to_sheet_row,
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
                "SL0017",
                "2026-08-28 23:30",
            ],
        )
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
            bank="PIPN",
            method="Manual",
            status="WITHDRAW",
            brand="FUCKSPINVIPA",
            bsb="815000",
            pay_id="61414769587",
            bank_lock="1",
        )
        row = to_sheet_row(txn, settings)
        self.assertEqual(row[3], "Timothy David Evans")
        self.assertEqual(row[4], "530.27")
        self.assertEqual(row[5], "Withdraw")
        self.assertEqual(row[6], "17113600239")
        self.assertEqual(row[8], "815000")
        self.assertEqual(row[9], "A51088178")
        self.assertEqual(row[10], "61414769587")
        self.assertEqual(normalize_status("WITHDRAW"), "Withdraw")


if __name__ == "__main__":
    unittest.main()
