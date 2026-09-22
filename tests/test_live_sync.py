import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.live_sync import live_interval_seconds, run_live_completed
from tests.test_dashboard_api import SAMPLE_ROW, _settings


COMPLETED_PAYLOAD = {
    "transactions": [SAMPLE_ROW],
    "totalCount": 1,
    "totalPage": 1,
    "totalAmount": 530.27,
}


def _wait_for_gathered(events: list[dict], stop: threading.Event) -> None:
    for _ in range(40):
        if any(event.get("status") == "Gathered" for event in events):
            break
        threading.Event().wait(0.05)
    else:
        stop.set()
    gathered_before_stop = any(event.get("status") == "Gathered" for event in events)
    stop.set()
    if not gathered_before_stop:
        raise AssertionError(
            "Completed rows must appear in the GUI while Automated Run is still on"
        )


class LiveSyncTests(unittest.TestCase):
    def test_interval_is_clamped_to_one_or_two_seconds(self) -> None:
        self.assertEqual(live_interval_seconds(1), 1.0)
        self.assertEqual(live_interval_seconds(2), 2.0)
        self.assertEqual(live_interval_seconds(60), 2.0)
        self.assertEqual(live_interval_seconds(0), 1.0)
        self.assertEqual(live_interval_seconds("nope"), 1.0)

    def test_live_loop_emits_gathered_then_stops(self) -> None:
        events: list[dict] = []
        stop = threading.Event()
        posted: list[dict] = []

        class FakeClient:
            def post(self, _path: str, data: dict[str, str]):
                posted.append(dict(data))
                return COMPLETED_PAYLOAD

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(database_path=Path(folder) / "gathering.db")
            with patch(
                "src.live_sync.DashboardClient.from_settings",
                return_value=FakeClient(),
            ):
                thread = threading.Thread(
                    target=run_live_completed,
                    kwargs={
                        "settings": settings,
                        "on_event": events.append,
                        "write_sheet": False,
                        "interval": 1,
                        "stop_event": stop,
                    },
                    daemon=True,
                )
                thread.start()
                _wait_for_gathered(events, stop)
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        gathered = [event for event in events if event.get("status") == "Gathered"]
        self.assertTrue(gathered)
        self.assertEqual(gathered[0]["transaction_id"], "17113600239")
        self.assertEqual(gathered[0]["detail"], "Live Completed")
        self.assertTrue(posted)
        self.assertEqual(posted[0]["status"], "COMPLETED")

    def test_live_loop_writes_completed_to_google_sheet(self) -> None:
        events: list[dict] = []
        stop = threading.Event()
        written: list[tuple[str, str]] = []

        class FakeClient:
            def post(self, _path: str, _data: dict[str, str]):
                return COMPLETED_PAYLOAD

        class FakeWriter:
            def __init__(self, _settings, _on_event) -> None:
                self.clients = [object()]

            def push(self, _db, txn, day: str) -> None:
                written.append((txn.transaction_id, day))

            def push_many(self, db, txns, day: str) -> None:
                for txn in txns:
                    self.push(db, txn, day)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(database_path=Path(folder) / "gathering.db")
            with (
                patch(
                    "src.live_sync.DashboardClient.from_settings",
                    return_value=FakeClient(),
                ),
                patch("src.live_sync.LiveSheetWriter", FakeWriter),
            ):
                thread = threading.Thread(
                    target=run_live_completed,
                    kwargs={
                        "settings": settings,
                        "on_event": events.append,
                        "write_sheet": True,
                        "interval": 1,
                        "stop_event": stop,
                    },
                    daemon=True,
                )
                thread.start()
                _wait_for_gathered(events, stop)
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        self.assertEqual(written, [("17113600239", settings.filter_date_from)])

    def test_live_loop_falls_back_to_logged_in_page(self) -> None:
        events: list[dict] = []
        stop = threading.Event()

        class FailingHttp:
            last_error = "dashboard list HTTP failed"

            def post(self, _path: str, _data: dict[str, str]):
                return None

        class FakeBrowser:
            closed = False

            def __init__(self, _settings, _on_event=None) -> None:
                self.last_error = ""

            def start(self) -> None:
                return None

            def post(self, _path: str, data: dict[str, str]):
                self.last_status = data.get("status")
                return COMPLETED_PAYLOAD

            def close(self) -> None:
                type(self).closed = True

        FakeBrowser.closed = False
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings = _settings(database_path=Path(folder) / "gathering.db")
            with (
                patch(
                    "src.live_sync.DashboardClient.from_settings",
                    return_value=FailingHttp(),
                ),
                patch("src.live_sync.LiveBrowser", FakeBrowser),
            ):
                thread = threading.Thread(
                    target=run_live_completed,
                    kwargs={
                        "settings": settings,
                        "on_event": events.append,
                        "write_sheet": False,
                        "interval": 1,
                        "stop_event": stop,
                    },
                    daemon=True,
                )
                thread.start()
                _wait_for_gathered(events, stop)
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        gathered = [event for event in events if event.get("status") == "Gathered"]
        self.assertTrue(gathered)
        self.assertTrue(
            any(
                "Switching to a slim logged-in dashboard window" in str(event.get("message") or "")
                for event in events
            )
        )
        self.assertTrue(FakeBrowser.closed)


if __name__ == "__main__":
    unittest.main()
