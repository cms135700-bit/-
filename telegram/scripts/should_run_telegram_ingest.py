#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_FILE = PROJECT_ROOT / "telegram" / "state" / "telegram_bot_state.json"
DEFAULT_INTERVAL_HOURS = 20
DEFAULT_TIMEZONE = "Asia/Seoul"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exit 0 when Telegram ingest should run, 78 when it should be skipped."
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"State file to inspect (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=DEFAULT_INTERVAL_HOURS,
        help=f"Minimum interval between successful polls (default: {DEFAULT_INTERVAL_HOURS})",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone for current time comparison (default: {DEFAULT_TIMEZONE})",
    )
    return parser.parse_args()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main() -> None:
    args = parse_args()
    state = load_state(args.state_file.expanduser().resolve())
    last_run = parse_iso_datetime(state.get("last_successful_poll_at"))
    if last_run is None:
        print("No previous successful poll found; ingest should run.")
        return

    now = datetime.now(tz=ZoneInfo(args.timezone))
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=ZoneInfo(args.timezone))

    elapsed = now - last_run
    required = timedelta(hours=args.interval_hours)
    if elapsed >= required:
        print(f"Last poll was {elapsed.total_seconds() / 3600:.2f} hours ago; ingest should run.")
        return

    remaining = required - elapsed
    print(
        "Skipping ingest: last successful poll was "
        f"{elapsed.total_seconds() / 3600:.2f} hours ago; "
        f"{remaining.total_seconds() / 3600:.2f} hours remain."
    )
    raise SystemExit(78)


if __name__ == "__main__":
    main()
