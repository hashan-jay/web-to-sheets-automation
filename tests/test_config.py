import unittest

from src.config import (
    LOGIN_ACCOUNT_SLOTS,
    google_sheet_url,
    load_gui_theme,
    login_account_keys,
    login_slot,
    normalize_dashboard_url,
    normalize_google_sheet_id,
)


class LoginAccountTests(unittest.TestCase):
    def test_login_slot_clamps_to_three(self) -> None:
        self.assertEqual(login_slot(1), 1)
        self.assertEqual(login_slot("3"), 3)
        self.assertEqual(login_slot("9"), 1)
        self.assertEqual(login_slot(""), 1)
        self.assertEqual(tuple(LOGIN_ACCOUNT_SLOTS), (1, 2, 3))

    def test_login_account_keys(self) -> None:
        keys = login_account_keys(2)
        self.assertEqual(keys["website"], "LOGIN_2_URL")
        self.assertEqual(keys["username"], "LOGIN_2_USERNAME")
        self.assertEqual(keys["password"], "LOGIN_2_PASSWORD")
        self.assertEqual(keys["twofa"], "LOGIN_2_2FA")

    def test_normalize_dashboard_url(self) -> None:
        self.assertEqual(
            normalize_dashboard_url("skgaming16.as6868.com/#login"),
            "https://skgaming16.as6868.com/#transactions",
        )
        self.assertEqual(
            normalize_dashboard_url("https://other.example.com"),
            "https://other.example.com/#transactions",
        )
        self.assertEqual(normalize_dashboard_url(""), "")

    def test_normalize_google_sheet_id(self) -> None:
        self.assertEqual(
            normalize_google_sheet_id(
                "https://docs.google.com/spreadsheets/d/1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo/edit"
            ),
            "1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo",
        )
        self.assertEqual(
            normalize_google_sheet_id("1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo"),
            "1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo",
        )
        self.assertEqual(normalize_google_sheet_id(""), "")
        self.assertIn(load_gui_theme(), {"light", "dark"})
        self.assertTrue(
            google_sheet_url("1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo").endswith(
                "/1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo"
            )
        )


if __name__ == "__main__":
    unittest.main()
