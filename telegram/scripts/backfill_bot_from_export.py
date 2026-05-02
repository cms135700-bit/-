#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from import_telegram_export import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    copy_or_move_file,
    find_attachment_paths,
    flatten_text,
    load_export_chats,
    parse_export_datetime,
)
from knowledge_pipeline.schemas import RawManifestRecord  # noqa: E402
from knowledge_pipeline.telegram_pipeline import manifest_path_for_note  # noqa: E402
from knowledge_pipeline.utils import append_jsonl_record, sha256_text  # noqa: E402
from telegram_bot_ingest import (  # noqa: E402
    DEFAULT_TIMEZONE,
    FileSpec,
    analyze_message,
    ensure_note_exists,
    markdown_asset_line,
    message_block,
    note_header,
    sanitize_filename,
    should_skip_image_storage,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Telegram Desktop export messages into the bot note structure."
    )
    parser.add_argument(
        "export_dir",
        type=Path,
        help="Directory that contains Telegram Desktop export files, including result.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Destination root for imported notes (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone used when grouping messages by date (default: {DEFAULT_TIMEZONE})",
    )
    parser.add_argument(
        "--chat-id",
        help="Specific chat id from the export to backfill",
    )
    parser.add_argument(
        "--chat-name",
        help="Specific chat name from the export to backfill",
    )
    parser.add_argument(
        "--target-slug",
        help="Override target bot slug, e.g. cms-6746975543",
    )
    parser.add_argument(
        "--move-media",
        action="store_true",
        help="Move exported media files into the destination instead of copying them",
    )
    parser.add_argument(
        "--list-chats",
        action="store_true",
        help="List chats found in the export and exit",
    )
    return parser.parse_args()


def export_chat_name(chat: dict[str, Any]) -> str:
    name = chat.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"chat-{chat.get('id', 'unknown')}"


def export_chat_id(chat: dict[str, Any]) -> str:
    return str(chat.get("id", "unknown")).strip() or "unknown"


def select_chat(chats: list[dict[str, Any]], chat_id: str | None, chat_name: str | None) -> dict[str, Any]:
    selected = chats
    if chat_id:
        selected = [chat for chat in selected if export_chat_id(chat) == str(chat_id)]
    if chat_name:
        chat_name_lower = chat_name.casefold()
        selected = [chat for chat in selected if export_chat_name(chat).casefold() == chat_name_lower]

    if not selected:
        raise SystemExit("No matching chat found in the export.")
    if len(selected) > 1:
        summary = ", ".join(f"{export_chat_name(chat)} ({export_chat_id(chat)})" for chat in selected[:10])
        raise SystemExit(
            "Multiple chats matched. Re-run with --chat-id or --chat-name. Matches: "
            f"{summary}"
        )
    return selected[0]


def bot_chat_shape(chat: dict[str, Any]) -> dict[str, Any]:
    name = export_chat_name(chat)
    chat_id = export_chat_id(chat)
    chat_type = str(chat.get("type") or "private")
    if chat_type == "private":
        return {"id": chat_id, "type": chat_type, "first_name": name}
    return {"id": chat_id, "type": chat_type, "title": name}


def target_slug_for_chat(chat: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    return f"{sanitize_filename(export_chat_name(chat).lower().replace(' ', '-'))}-{export_chat_id(chat)}"


def export_forward_summary(message: dict[str, Any]) -> str:
    candidate = message.get("forwarded_from")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    if isinstance(candidate, dict):
        for key in ("title", "name", "from"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("forwarded_from_name", "saved_from", "via_bot"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
    }
    return mapping.get(suffix, "application/octet-stream")


def build_file_specs(export_dir: Path, message: dict[str, Any]) -> list[FileSpec]:
    specs: list[FileSpec] = []
    for raw_attachment in find_attachment_paths(message):
        source = export_dir / raw_attachment
        mime_type = mime_type_for_path(source)
        specs.append(
            FileSpec(
                kind="export",
                file_id=raw_attachment,
                filename_hint=source.name,
                mime_type=mime_type,
            )
        )
    return specs


def store_export_message(
    *,
    export_dir: Path,
    chat: dict[str, Any],
    target_slug: str,
    message: dict[str, Any],
    output_root: Path,
    timezone: ZoneInfo,
    move_media: bool,
) -> tuple[bool, Path]:
    chat_shape = bot_chat_shape(chat)
    parsed_dt = parse_export_datetime(str(message["date"]), timezone)
    date_key = parsed_dt.date().isoformat()
    note_dir = output_root / "bot" / target_slug / date_key[:4] / date_key[5:7]
    note_path = note_dir / f"{date_key}.md"
    asset_dir = note_dir / f"{date_key}_assets"
    message_key = f"{export_chat_id(chat)}:{message.get('id', 'unknown')}"
    current_content = ensure_note_exists(note_path, note_header(chat_shape, date_key))

    if f"`{message_key}`" in current_content:
        return False, note_path

    text = flatten_text(message.get("text"))
    forward_from = export_forward_summary(message)
    file_specs = build_file_specs(export_dir, message)
    analysis = analyze_message(text, forward_from, file_specs)

    attachment_lines: list[str] = []
    absolute_attachments: list[str] = []
    for index, raw_attachment in enumerate(find_attachment_paths(message), start=1):
        source = export_dir / raw_attachment
        if not source.exists():
            continue
        mime_type = mime_type_for_path(source)
        spec = FileSpec(
            kind="export",
            file_id=raw_attachment,
            filename_hint=source.name,
            mime_type=mime_type,
        )
        if should_skip_image_storage(text, spec):
            continue
        hint_stem = sanitize_filename(source.stem or "file")
        destination_name = f"{message.get('id', 'msg')}_{index}_{hint_stem}{source.suffix}"
        destination = asset_dir / destination_name
        copy_or_move_file(source, destination, move_media=move_media)
        relative_path = Path(f"./{date_key}_assets") / destination.name
        attachment_lines.append(markdown_asset_line(relative_path, mime_type_for_path(destination)))
        absolute_attachments.append(str(destination.resolve()))

    block = message_block(
        message_key=message_key,
        timestamp=parsed_dt.strftime("%H:%M:%S"),
        sender=export_chat_name(chat) if str(message.get("from") or "").strip() == "" else str(message.get("from")).strip(),
        forward_from=forward_from,
        text=text,
        analysis=analysis,
        attachments=attachment_lines,
    )
    separator = "" if current_content.endswith("\n\n") else "\n"
    note_path.write_text(f"{current_content}{separator}{block}", encoding="utf-8")

    append_jsonl_record(
        manifest_path_for_note(note_path),
        RawManifestRecord(
            source_type="telegram_export_backfill",
            source_path=str(note_path.resolve()),
            message_key=message_key,
            date=date_key,
            sender=str(message.get("from") or export_chat_name(chat)),
            forwarded_from=forward_from or None,
            attachments=absolute_attachments,
            hash=sha256_text(message_key, str(message.get("from") or export_chat_name(chat)), forward_from, text, "".join(attachment_lines)),
        ),
        dedupe_key="message_key",
    )
    return True, note_path


def main() -> None:
    args = parse_args()
    export_dir = args.export_dir.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    timezone = ZoneInfo(args.timezone)
    chats = load_export_chats(export_dir)

    if args.list_chats:
        for chat in chats:
            print(f"{export_chat_id(chat)}\t{export_chat_name(chat)}")
        return

    chat = select_chat(chats, args.chat_id, args.chat_name)
    target_slug = target_slug_for_chat(chat, args.target_slug)

    stored_count = 0
    changed_notes: set[Path] = set()
    for message in chat.get("messages", []):
        if not isinstance(message, dict):
            continue
        if message.get("type") != "message":
            continue
        if not isinstance(message.get("date"), str) or not str(message["date"]).strip():
            continue
        stored, note_path = store_export_message(
            export_dir=export_dir,
            chat=chat,
            target_slug=target_slug,
            message=message,
            output_root=output_root,
            timezone=timezone,
            move_media=args.move_media,
        )
        if stored:
            stored_count += 1
            changed_notes.add(note_path)

    print(
        f"Backfilled {stored_count} message(s) into {len(changed_notes)} note(s) under {output_root / 'bot' / target_slug}"
    )


if __name__ == "__main__":
    main()
