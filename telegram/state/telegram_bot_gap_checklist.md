# Telegram Bot Gap Checklist

_Generated: 2026-04-24 (Asia/Seoul)_

This checklist tracks the currently missing `message_key` ranges for the bot chat `cms-6746975543`.
It reflects the raw notes that actually exist on disk now, not every historical warning in the gap audit log.

## Current Range

- chat_id: `6746975543`
- stored message count: `240`
- stored min/max: `5` to `401`
- current state file: [telegram_bot_state.json](/Users/ppingkku/Documents/stock/telegram/state/telegram_bot_state.json)
- raw note root: [cms-6746975543](/Users/ppingkku/Documents/stock/telegram/bot/cms-6746975543)

## Actual Missing Ranges

1. `9-10` (size `2`)
2. `22-33` (size `12`)
3. `51-51` (size `1`)
4. `78-82` (size `5`)
5. `112-137` (size `26`)
6. `150-155` (size `6`)
7. `164-184` (size `21`)
8. `204-223` (size `20`)
9. `228-234` (size `7`)
10. `246-252` (size `7`)
11. `281-298` (size `18`)
12. `300-307` (size `8`)
13. `316-316` (size `1`)
14. `327-339` (size `13`)
15. `360-363` (size `4`)
16. `388-393` (size `6`)

## What This Means

- The checklist above is based on the actual stored bot notes.
- Historical gap warnings can include false positives if a previous run wrote the raw note before state reconciliation finished.
- That happened once around `385`; it is **not** missing from the stored notes anymore, so it does not appear in this checklist.

## Raw Update Reality Check

- Stored raw Bot API updates currently cover `message_id 254-401`.
- Within that raw-update window, these ranges were also absent from the fetched Bot API payloads:
  - `281-298`
  - `300-307`
  - `316-316`
  - `327-339`
  - `360-363`
  - `388-393`
- That means those ranges are not just missing from markdown rendering. They were not present in the fetched Bot API update stream preserved in this workspace.

## Why Gaps Keep Happening

- Telegram Bot API only retains pending updates for a limited time, typically about 24 hours.
- Several recent manual polls happened after that window:
  - `2026-04-20 23:49 KST`
  - `2026-04-22 15:25 KST`
  - `2026-04-24 01:19 KST`
- When polling happens after the retention window, older pending updates may already be gone before the script can fetch them.
- The ingest script now:
  - warns when the last successful poll is stale,
  - logs fetched batch ranges,
  - reconciles `last_message_id_by_chat` from raw notes on startup,
  - appends real jump detections to [telegram_bot_gap_audit.jsonl](/Users/ppingkku/Documents/stock/telegram/state/telegram_bot_gap_audit.jsonl).

## Recovery Path

The only reliable way to fill the real missing ranges is a Telegram Desktop JSON export plus backfill.

1. Export the bot chat from Telegram Desktop as `JSON`.
2. Run [backfill_bot_from_export.py](/Users/ppingkku/Documents/stock/telegram/scripts/backfill_bot_from_export.py) against that export.
3. Re-check this checklist and confirm whether each range was filled.

Ready command:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/backfill_bot_from_export.py \
  /path/to/telegram_export \
  --chat-id 6746975543 \
  --target-slug cms-6746975543
```
