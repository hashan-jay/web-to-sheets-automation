import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database import GatheringDB
from src.models import Transaction
from src.pipeline import transactions_for_date
from src.workspace import (
    apply_workspace_to_settings,
    empty_workspace_state,
    load_workspace_state,
    migrate_legacy_workspace,
    save_workspace_state,
    seed_workspace_sheets,
    website_host,
    workspace_database_path,
    workspace_key,
)


class WorkspaceKeyTests(unittest.TestCase):
    def test_website_host_from_full_url(self) -> None:
        self.assertEqual(
            website_host("https://skgaming4.as6868.com/#transactions"),
            "skgaming4.as6868.com",
        )
        self.assertEqual(website_host("skgaming16.as6868.com/#login"), "skgaming16.as6868.com")
        self.assertEqual(website_host(""), "")

    def test_workspace_key_is_per_website_and_username(self) -> None:
        first = workspace_key("https://skgaming4.as6868.com/#transactions", "sk4srl")
        second = workspace_key("https://skgaming16.as6868.com/#transactions", "sk4srl")
        third = workspace_key("https://skgaming4.as6868.com/#transactions", "other")
        same = workspace_key("skgaming4.as6868.com/#login", "SK4SRL")
        self.assertEqual(first, "skgaming4.as6868.com__sk4srl")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(first, same)
        self.assertEqual(workspace_key("", ""), "")


class WorkspaceStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.sites = self.root / "sites"
        self.patches = [
            patch("src.workspace.SITES_DIR", self.sites),
            patch("src.workspace.LEGACY_DATABASE", self.root / "gathering.db"),
            patch("src.workspace.LEGACY_AUTH", self.root / "auth_state.json"),
            patch("src.workspace.LEGACY_MARKER", self.sites / ".legacy_migrated"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def test_save_and_load_sheets_per_workspace(self) -> None:
        key = "skgaming4.as6868.com__sk4srl"
        save_workspace_state(
            key,
            website="https://skgaming4.as6868.com/#transactions",
            username="sk4srl",
            sheet_ids=["1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo", ""],
        )
        other = "skgaming16.as6868.com__other"
        save_workspace_state(
            other,
            website="https://skgaming16.as6868.com/#transactions",
            username="other",
            sheet_ids=["1otherSheetIdxxxxxxxxxxxxxxxYYYY", ""],
        )
        first = load_workspace_state(key)
        second = load_workspace_state(other)
        self.assertEqual(first["username"], "sk4srl")
        self.assertEqual(
            first["sheet_ids"][0],
            "1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo",
        )
        self.assertEqual(second["sheet_ids"][0], "1otherSheetIdxxxxxxxxxxxxxxxYYYY")
        self.assertEqual(load_workspace_state(""), empty_workspace_state())

    def test_save_and_load_sheet_brands_per_workspace(self) -> None:
        key = "jkkbm77.u55y38.com__kaboom77finwd"
        save_workspace_state(
            key,
            website="https://jkkbm77.u55y38.com/#transactions",
            username="kaboom77finWD",
            sheet_brands=["KABOOM77", "KABOOM77", " kaboom77 "],
        )
        other = "skgaming4.as6868.com__sk4srl"
        save_workspace_state(
            other,
            website="https://skgaming4.as6868.com/#transactions",
            username="sk4srl",
            sheet_brands=["FUCKSPIN\nPOKIESPARK", "JOINTMATE"],
        )
        first = load_workspace_state(key)
        second = load_workspace_state(other)
        self.assertEqual(first["sheet_brands"], ["KABOOM77"])
        self.assertEqual(second["sheet_brands"], ["FUCKSPIN", "POKIESPARK", "JOINTMATE"])
        from src.config import Settings

        settings = Settings.load()
        apply_workspace_to_settings(settings, key)
        self.assertEqual(settings.sheet_brands, ("KABOOM77",))

    def test_save_and_load_sheet_start_rows(self) -> None:
        key = "skgaming23.as6868.com__sk23finance111"
        save_workspace_state(key, deposit_start_row=200, withdraw_start_row=2500)
        state = load_workspace_state(key)
        self.assertEqual(state["deposit_start_row"], 200)
        self.assertEqual(state["withdraw_start_row"], 2500)
        save_workspace_state(key, deposit_start_row=1800, withdraw_start_row=1024)
        fixed = load_workspace_state(key)
        self.assertEqual(fixed["deposit_start_row"], 1800)
        self.assertEqual(fixed["withdraw_start_row"], 1801)
        from src.config import Settings

        settings = Settings.load()
        apply_workspace_to_settings(settings, key)
        self.assertEqual(settings.deposit_start_row, 1800)
        self.assertEqual(settings.withdraw_start_row, 1801)

    def test_seed_sheets_only_when_empty(self) -> None:
        key = "site__user"
        seeded = seed_workspace_sheets(key, ["1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo"])
        again = seed_workspace_sheets(key, ["1shouldNotReplaceThisSheetxxxxx"])
        self.assertEqual(seeded["sheet_ids"][0], "1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo")
        self.assertEqual(again["sheet_ids"][0], "1BAXqHMZAP9-sVXGn_up32CkmOmwLiPAxDnYf3yqZiRo")

    def test_migrate_legacy_once_to_first_website_only(self) -> None:
        legacy = self.root / "gathering.db"
        legacy.write_text("legacy-db", encoding="utf-8")
        first = "skgaming4.as6868.com__sk4srl"
        second = "skgaming16.as6868.com__other"
        self.assertTrue(migrate_legacy_workspace(first))
        self.assertTrue(workspace_database_path(first).exists())
        self.assertEqual(workspace_database_path(first).read_text(encoding="utf-8"), "legacy-db")
        self.assertFalse(migrate_legacy_workspace(second))
        self.assertFalse(workspace_database_path(second).exists())
        self.assertFalse(migrate_legacy_workspace(first))

    def test_apply_workspace_points_settings_at_site_db(self) -> None:
        from src.config import Settings

        settings = Settings.load()
        key = "skgaming4.as6868.com__sk4srl"
        apply_workspace_to_settings(settings, key)
        self.assertEqual(settings.database_path, workspace_database_path(key))
        self.assertEqual(settings.auth_state_path.name, "auth_state.json")
        self.assertIn(key, str(settings.database_path))


class WorkspaceDatabaseIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.one = GatheringDB(Path(self.tmp.name) / "one" / "gathering.db")
        self.two = GatheringDB(Path(self.tmp.name) / "two" / "gathering.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_transactions_stay_on_their_own_website_db(self) -> None:
        self.one.ingest(
            [
                Transaction(
                    transaction_id="111",
                    datetime="2026-09-08 10:00",
                    status="DEPOSIT",
                    amount="10",
                )
            ]
        )
        self.two.ingest(
            [
                Transaction(
                    transaction_id="222",
                    datetime="2026-09-08 11:00",
                    status="WITHDRAW",
                    amount="5",
                )
            ]
        )
        self.assertEqual(
            [row.transaction_id for row in transactions_for_date(self.one, "2026-09-08")],
            ["111"],
        )
        self.assertEqual(
            [row.transaction_id for row in transactions_for_date(self.two, "2026-09-08")],
            ["222"],
        )
        self.assertEqual(self.one.counts()["pending"], 1)
        self.assertEqual(self.two.counts()["pending"], 1)
        self.assertEqual(len(self.one.all_records()), 1)
        self.assertEqual(len(self.two.all_records()), 1)


if __name__ == "__main__":
    unittest.main()
