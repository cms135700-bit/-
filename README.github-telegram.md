# Telegram Bot Ingest Automation

This repository is prepared to run the existing Telegram Bot API ingest workflow on GitHub Actions.

## What It Does

- Polls the configured Telegram bot inbox with `telegram/scripts/telegram_bot_ingest.py`.
- Stores new bot messages under `telegram/bot/`.
- Writes research-assistant notes under `telegram/ra/`.
- Updates incremental state in `telegram/state/telegram_bot_state.json`.
- Runs from GitHub Actions every 4 hours, but only proceeds when at least 20 hours have passed since the last successful poll.

## GitHub Secret

Add this repository secret in GitHub:

```text
TELEGRAM_BOT_TOKEN
```

Do not commit `telegram/.env`.

## Manual Run

```bash
python3 telegram/scripts/telegram_bot_ingest.py
```

## Schedule

The workflow is in `.github/workflows/telegram-bot-ingest.yml`.

GitHub cron cannot express a true rolling 20-hour interval cleanly, so the workflow wakes every 4 hours and `telegram/scripts/should_run_telegram_ingest.py` skips the run until the 20-hour interval has elapsed.

## Git Tracking

The root `.gitignore` is intentionally restrictive. It allows the automation code, bot markdown notes, RA notes, manifests, raw update logs, and state files, while excluding secrets, caches, local vault files, generated explain/normalized notes, and downloaded media asset folders.
