# Telegram Workflows

`telegram/scripts/import_telegram_export.py` imports a Telegram Desktop JSON export into date-based markdown notes under this folder.

`telegram/scripts/telegram_bot_ingest.py` polls a Telegram bot inbox and stores the messages you send to the bot as daily markdown notes.

`telegram/scripts/backfill_bot_from_export.py` backfills missing bot-note messages from a Telegram Desktop JSON export.

## Input

- Export from Telegram Desktop as `JSON`.
- The export directory must contain `result.json`.
- Media files are copied next to the generated daily note by default.

## Output

Generated notes are written here:

```text
telegram/
  chats/
    <chat-slug>/
      YYYY/
        MM/
          YYYY-MM-DD.md
          YYYY-MM-DD_assets/
```

If `--archive-export` is used, the original export folder is also copied to `telegram/raw/<export-name>/`.

Both import paths now also emit `raw_manifest` records for downstream normalization:

```text
telegram/
  manifests/
    chats/
      <chat-slug>.raw_manifest.jsonl
    bot/
      <chat-slug>.raw_manifest.jsonl
```

## Usage

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/import_telegram_export.py \
  ~/Downloads/telegram_export
```

Move media files instead of copying them:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/import_telegram_export.py \
  ~/Downloads/telegram_export \
  --move-media
```

Archive the original export folder under `telegram/raw/` while importing:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/import_telegram_export.py \
  ~/Downloads/telegram_export \
  --archive-export
```

## Notes

- The script supports both a single-chat export (`messages` at top level) and a full export (`chats.list`).
- Daily grouping uses `Asia/Seoul` by default. Override it with `--timezone`.
- Only message rows are imported. Service events such as join/leave records are skipped.

## Bot Ingest

This workflow is for new messages that you send to your bot. It does not read old chat history or `Saved Messages` directly.

### Setup

1. Open `@BotFather` in Telegram and create a bot with `/newbot`.
2. Copy the bot token.
3. Copy the example file and put the token into `telegram/.env`:

```bash
cp /Users/ppingkku/Documents/stock/telegram/.env.example \
  /Users/ppingkku/Documents/stock/telegram/.env
```

Edit `/Users/ppingkku/Documents/stock/telegram/.env` and replace the value:

```env
TELEGRAM_BOT_TOKEN=123456:your_bot_token
```

4. Open a chat with your bot and send a test message, image, or document.

### Output

Bot-ingested notes are written here:

```text
telegram/
  bot/
    <chat-slug>/
      YYYY/
        MM/
          YYYY-MM-DD.md
          YYYY-MM-DD_assets/
  ra/
    <chat-slug>/
      YYYY/
        MM/
          YYYY-MM-DD.md
  explain/
    <chat-slug>/
      YYYY/
        MM/
          YYYY-MM-DD.md
  state/
    telegram_bot_state.json
    telegram_bot_gap_audit.jsonl
  raw_updates/
    telegram_bot/
      YYYY/
        MM/
          YYYY-MM-DD.raw_updates.jsonl
```

Each appended message also writes a deterministic `raw_manifest` record under `telegram/manifests/bot/`.
Each fetched Telegram update is also archived as raw JSONL under `telegram/raw_updates/telegram_bot/` so you can inspect what the Bot API actually delivered when diagnosing missing or filtered messages.
The `bot/` note remains the immutable raw capture layer. The `ra/` note is a second-layer research note that rewrites each incoming message into your investment framework.
The `explain/` note is a supplementary explanation layer that is written manually in Codex sessions after new Telegram messages are ingested.

### Usage

Fetch pending updates once and exit:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/telegram_bot_ingest.py
```

In one-shot mode, the script does not wait for future messages. It drains the updates that are already pending at the moment the run starts, logs each fetched batch, and exits after the queue is empty.

Keep polling for new messages until interrupted:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/telegram_bot_ingest.py \
  --watch
```

### What Gets Saved

- `text` messages are appended to the daily raw markdown note under `telegram/bot/`.
- `caption` text is saved for photos/documents.
- Images are embedded into markdown.
- Documents, audio, video, and similar files are downloaded and linked.
- Images tagged with `#일봉` or `#주봉` are not stored as attachments; the message text and metadata are still captured.
- Forwarded messages keep a `forwarded_from` metadata line when Telegram exposes it.
- Each raw message block is structured with `항목`, `유형`, `요약`, `해석`, `핵심`, and the original text when available.
- Each message also creates or updates a daily RA note under `telegram/ra/` with `TL;DR`, `What Matters`, `My Take`, `Do Not Misread`, `Facts That Matter`, `Better Expression`, `Counterarguments`, `Next Checkpoints`, and `Invalidation`.
- Supplementary explanations under `telegram/explain/` are generated manually after capture, using the current investment prompt rules.

### Limits

- Only new inbound bot updates are ingested.
- Edited messages are not rewritten in-place.
- Existing duplicates are avoided by storing a `message_key` marker inside the daily note.
- The script reads `TELEGRAM_BOT_TOKEN` from `/Users/ppingkku/Documents/stock/telegram/.env` by default.
- Telegram Bot API pending updates are typically retained for up to about 24 hours, so manual polling after a long gap can permanently miss older messages.
- The ingest script now warns when the last successful poll is stale and appends any detected `message_id` jumps to `telegram/state/telegram_bot_gap_audit.jsonl`.
- One-shot runs now print batch-level progress to stderr so it is easier to see whether the queue is still being drained or already empty.

### Explain Layer

The prompt used when writing supplementary explanations lives here:

```text
/Users/ppingkku/Documents/stock/telegram/prompts/telegram_explain_system_prompt.md
```

The intended workflow is:

1. Ingest new Telegram updates into `telegram/bot/` and `telegram/ra/`
2. Review the newly added raw messages in Codex
3. Write or update the corresponding daily explanation note under `telegram/explain/`

## Export Backfill

Use this when some historical bot-chat messages were missed and you want to recover them from a Telegram Desktop export.

1. Export the bot chat from Telegram Desktop as `JSON`.
2. Run:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/backfill_bot_from_export.py \
  /path/to/telegram_export \
  --chat-id 6746975543 \
  --target-slug cms-6746975543
```

If you are not sure which chat id is in the export:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/backfill_bot_from_export.py \
  /path/to/telegram_export \
  --list-chats
```

The backfill script appends only missing `message_key`s, so it is safe to re-run on the same export.

## Knowledge Pipeline Handoff

Raw Telegram notes stay immutable. Normalize them into `evidence_packet` artifacts before synthesis or note filing.

Backfill missing manifests for already imported Telegram notes:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/backfill_raw_manifests.py \
  --vault-root /Users/ppingkku/Documents/stock
```

Normalize one Telegram day note:

```bash
python3 /Users/ppingkku/Documents/stock/telegram/scripts/normalize_telegram_day.py \
  /Users/ppingkku/Documents/stock/telegram/bot/cms-6746975543/2026/03/2026-03-25.md
```

The normalized output is written here by default:

```text
telegram/
  normalized/
    <chat-slug>/
      YYYY/
        MM/
          YYYY-MM-DD.evidence_packet.json
```
