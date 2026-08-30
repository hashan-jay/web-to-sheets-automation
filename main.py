from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import Settings
from src.errors import ConfigError
from src.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape admin transactions and append new rows to Google Sheets."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and print rows without writing to Google Sheets.",
    )
    parser.add_argument("--headless", action="store_true", help="Hide the browser window.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    parser.add_argument("--date", help="Override FILTER_DATE_FROM (YYYY-MM-DD).")
    parser.add_argument("--limit", type=int, help="Only keep the first N scraped transactions.")
    parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Skip the dashboard and copy pending gathering-database notifications.",
    )
    parser.add_argument(
        "--write-sheet",
        action="store_true",
        help="After scraping, write pending rows to Google Sheets.",
    )
    parser.add_argument(
        "--dump",
        type=Path,
        help="Optional path to save a JSON summary.",
    )
    return parser.parse_args()


def _print_event(event: dict) -> None:
    if event.get("kind") == "row":
        print(
            f"  [{event.get('status')}] {event.get('transaction_id')} "
            f"{event.get('name')} {event.get('amount')} — {event.get('detail')}"
        )
        return
    if event.get("message"):
        print(event["message"])


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    if args.date:
        settings.filter_date_from = args.date
    if args.headless:
        settings.headed = False
    if args.headed:
        settings.headed = True

    try:
        result = run_pipeline(
            settings,
            on_event=_print_event,
            dry_run=args.dry_run,
            limit=args.limit,
            scrape=not args.pending_only,
            write_sheet=args.pending_only or args.write_sheet,
        )
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc

    if args.dump:
        args.dump.write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        print(f"Wrote summary to {args.dump}")


if __name__ == "__main__":
    main()
