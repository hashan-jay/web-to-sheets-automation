from __future__ import annotations

import threading
import time
from src.config import Settings
from src.database import GatheringDB
from src.pipeline import EventFn, gather_from_dashboard


class NotificationWatcher:
    """Polls the gathering database and optionally the dashboard.

    New pending notifications trigger a copy-to-sheet run automatically.
    """

    def __init__(
        self,
        settings: Settings,
        on_event: EventFn,
        dry_run: bool = False,
        poll_interval: int = 60,
        db_check_seconds: float = 2.0,
    ) -> None:
        self.settings = settings
        self.on_event = on_event
        self.dry_run = dry_run
        self.poll_interval = max(int(poll_interval), 5)
        self.db_check_seconds = db_check_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="notification-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)
            self._thread = None

    def _loop(self) -> None:
        db = GatheringDB(self.settings.database_path)
        last_scrape = 0.0
        self.on_event(
            {
                "kind": "log",
                "message": (
                    f"Watcher started. Dashboard poll every {self.poll_interval}s. "
                    "It only scrapes into the GUI — it does not write the Google Sheet."
                ),
            }
        )
        while not self._stop.is_set():
            now = time.time()
            try:
                if self.settings.dashboard_url and now - last_scrape >= self.poll_interval:
                    gather_from_dashboard(self.settings, db, self.on_event)
                    last_scrape = now
                    self.on_event({"kind": "counts", "counts": db.counts()})
            except Exception as exc:
                self.on_event({"kind": "log", "message": f"Watcher error: {exc}"})
            self._stop.wait(self.db_check_seconds)
        self.on_event({"kind": "log", "message": "Watcher stopped."})


def run_watcher_forever(
    settings: Settings,
    on_event: EventFn | None = None,
    dry_run: bool = False,
) -> None:
    emit: EventFn = on_event or (lambda _event: None)
    watcher = NotificationWatcher(
        settings,
        emit,
        dry_run=dry_run,
        poll_interval=settings.poll_interval_seconds,
    )
    watcher.start()
    try:
        while watcher.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        watcher.stop()
