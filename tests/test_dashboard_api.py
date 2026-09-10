import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Settings
from src.dashboard_api import (
    LIVE_POST_JS,
    dashboard_origin,
    fetch_completed,
    list_filter_payload,
    load_api_session,
    looks_like_list_payload,
    post_with_page,
    transaction_from_api,
)
from src.models import Transaction
from src.scraper import scrape_transactions
from src.tally import COMPLETED_STATUS


def _settings(**overrides) -> Settings:
    values = {
        "dashboard_url": "https://skgaming4.as6868.com/#transactions",
        "dashboard_username": "demo",
        "dashboard_password": "secret",
        "dashboard_2fa": "",
        "manual_login_seconds": 0,
        "filter_date_from": "2026-09-08",
        "filter_date_to": "2026-09-08",
        "filter_type": "ACTIVE",
        "filter_status": COMPLETED_STATUS,
        "google_sheet_id": "",
        "google_worksheet": "",
        "google_credentials_path": Path("credentials/service-account.json"),
        "default_bank_account": "",
        "default_brand": "FUCKSPIN",
        "default_staff_code": "",
        "max_pages": 20,
        "use_dashboard_api": True,
    }
    values.update(overrides)
    return Settings(**values)


SAMPLE_ROW = {
    "id": 17113600239,
    "type": "WITHDRAW",
    "status": "COMPLETED",
    "cash": 530.27,
    "method": "Manual",
    "gateway": "BANK",
    "createdDateTime": "2026-08-29 10:44:11",
    "processedDateTime": "2026-08-29 10:44:33",
    "user": {
        "username": "A51088178",
        "name": "[JKFCKSPNAU] Timothy David Evans",
        "mobile": "61414769587",
        "tags": ["FUCKFUCKVIPC", "NETLOSSB"],
        "bank": json.dumps(
            {
                "bank": "PIPN",
                "accountName": "Timothy David Evans",
                "accountNumber": "100512365",
                "bsb": "815000",
                "payId": "61414769587",
                "lock": "1",
            }
        ),
    },
}


class DashboardApiTests(unittest.TestCase):
    def test_dashboard_origin(self) -> None:
        self.assertEqual(
            dashboard_origin("https://skgaming4.as6868.com/#transactions"),
            "https://skgaming4.as6868.com",
        )

    def test_list_filter_payload_matches_website_search(self) -> None:
        payload = list_filter_payload(_settings(), 2)
        self.assertEqual(payload["pageIndex"], "2")
        self.assertEqual(payload["includeAdmin"], "1")
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["type"], "ACTIVE")
        self.assertEqual(payload["sDate"], "2026-09-08 00:00:00")
        self.assertEqual(payload["eDate"], "2026-09-08 23:59:59")

    def test_transaction_from_api_maps_card_fields(self) -> None:
        txn = transaction_from_api(SAMPLE_ROW)
        self.assertEqual(txn.transaction_id, "17113600239")
        self.assertEqual(txn.username, "A51088178")
        self.assertEqual(txn.name, "[JKFCKSPNAU] Timothy David Evans")
        self.assertEqual(txn.amount, "530.27")
        self.assertEqual(txn.bank, "PIPN")
        self.assertEqual(txn.bank_account_name, "Timothy David Evans")
        self.assertEqual(txn.bank_account_number, "100512365")
        self.assertEqual(txn.bsb, "815000")
        self.assertEqual(txn.pay_id, "61414769587")
        self.assertEqual(txn.bank_lock, "1")
        self.assertEqual(txn.status, "WITHDRAW")
        self.assertEqual(txn.created, "2026-08-29 10:44")
        self.assertEqual(txn.processed, "2026-08-29 10:44")
        self.assertEqual(txn.brand, "FUCKFUCKVIPC")
        self.assertEqual(txn.attachment, "")
        with_receipt = dict(SAMPLE_ROW)
        with_receipt["type"] = "DEPOSIT"
        with_receipt["details"] = {"receipt": "https://cdn.example.com/deposit.jpg"}
        deposit = transaction_from_api(with_receipt)
        self.assertEqual(deposit.attachment, "https://cdn.example.com/deposit.jpg")
        self.assertEqual(deposit.extras.get("attachment"), "https://cdn.example.com/deposit.jpg")

    def test_looks_like_list_payload(self) -> None:
        self.assertTrue(
            looks_like_list_payload(
                {"transactions": [], "totalCount": 0, "totalPage": 0, "totalAmount": 0}
            )
        )
        self.assertFalse(looks_like_list_payload("<html>login</html>"))
        self.assertFalse(looks_like_list_payload({"ok": True}))

    def test_live_post_does_not_use_jquery(self) -> None:
        self.assertNotIn("jQuery", LIVE_POST_JS)
        self.assertIn("fetch(", LIVE_POST_JS)

    def test_post_with_page_uses_browser_request_not_jquery(self) -> None:
        payload = {
            "transactions": [SAMPLE_ROW],
            "totalCount": 1,
            "totalPage": 1,
            "totalAmount": 530.27,
        }

        class FakeResponse:
            status = 200

            def json(self):
                return payload

        class FakeRequest:
            def __init__(self) -> None:
                self.form = None
                self.headers = None

            def post(self, _url, form=None, headers=None, timeout=None):
                self.form = form
                self.headers = headers
                return FakeResponse()

        class FakePage:
            def __init__(self) -> None:
                self.request = FakeRequest()

            def evaluate(self, js, _data=None):
                text = str(js)
                if "fetch(" in text or "getAllTransactions" in text:
                    raise AssertionError("in-page fetch should not run when request works")
                return "tok"

        page = FakePage()
        result, error = post_with_page(
            page,
            "/transactions/getAllTransactions",
            {"status": "COMPLETED"},
            "https://skgaming4.as6868.com",
        )
        self.assertEqual(error, "")
        self.assertEqual(result["totalCount"], 1)
        self.assertNotIn("token", page.request.form or {})

    def test_load_api_session_reads_admin_token(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "auth_state.json"
            path.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "sid",
                                "value": "abc",
                                "domain": "skgaming4.as6868.com",
                                "path": "/",
                            }
                        ],
                        "origins": [
                            {
                                "origin": "https://skgaming4.as6868.com",
                                "localStorage": [
                                    {
                                        "name": "ADMIN",
                                        "value": json.dumps(
                                            {"id": "1", "token": "saved-token"}
                                        ),
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            session = load_api_session(_settings(auth_state_path=path))
            self.assertIsNotNone(session)
            self.assertEqual(session.origin, "https://skgaming4.as6868.com")
            self.assertEqual(session.token, "saved-token")
            self.assertEqual(session.cookies[0]["name"], "sid")

    def test_fetch_completed_pages_until_all_ids(self) -> None:
        pages = {
            0: {
                "transactions": [SAMPLE_ROW],
                "totalCount": 2,
                "totalPage": 2,
                "totalAmount": 580.27,
            },
            1: {
                "transactions": [
                    {
                        "id": "17113786958",
                        "type": "DEPOSIT",
                        "cash": 50,
                        "createdDateTime": "2026-08-29 12:13:00",
                        "user": {
                            "username": "A39759077",
                            "name": "Brock",
                            "bank": "{}",
                        },
                    }
                ],
                "totalCount": 2,
                "totalPage": 2,
                "totalAmount": 580.27,
            },
        }
        calls: list[int] = []

        def post(_path: str, data: dict[str, str]):
            index = int(data["pageIndex"])
            calls.append(index)
            return pages[index]

        capture = fetch_completed(post, _settings(), max_pages=20)
        self.assertIsNotNone(capture)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(capture.website_records, 2)
        self.assertEqual(capture.website_total, "580.27")
        self.assertEqual(
            [txn.transaction_id for txn in capture.transactions],
            ["17113600239", "17113786958"],
        )

    def test_fetch_completed_rejects_html(self) -> None:
        def post(_path: str, _data: dict[str, str]):
            return "<html>login</html>"

        self.assertIsNone(fetch_completed(post, _settings()))

    def test_fetch_skips_known_ids_on_first_page(self) -> None:
        pages = {
            0: {
                "transactions": [SAMPLE_ROW],
                "totalCount": 1,
                "totalPage": 1,
                "totalAmount": 530.27,
            }
        }

        def post(_path: str, data: dict[str, str]):
            return pages[int(data["pageIndex"])]

        capture = fetch_completed(
            post,
            _settings(),
            known_ids={"17113600239"},
            catch_up=False,
            quiet=True,
            expect_new=0,
        )
        self.assertIsNotNone(capture)
        self.assertEqual(capture.transactions, [])
        self.assertEqual(capture.website_records, 1)

    def test_scrape_uses_browser_filters_not_http_shortcut(self) -> None:
        with patch("src.scraper.sync_playwright") as playwright:
            playwright.side_effect = RuntimeError("browser scrape path")
            with self.assertRaisesRegex(RuntimeError, "browser scrape path"):
                scrape_transactions(_settings(use_open_browser=False))
        playwright.assert_called()


if __name__ == "__main__":
    unittest.main()
