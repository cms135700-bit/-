#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.schemas import RawManifestRecord
from knowledge_pipeline.telegram_pipeline import manifest_path_for_note
from knowledge_pipeline.utils import append_jsonl_record, sha256_text


DEFAULT_OUTPUT_ROOT = Path("/Users/ppingkku/Documents/stock/telegram")
DEFAULT_TIMEZONE = "Asia/Seoul"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
ATTACHMENT_KEYS = (
    "file",
    "photo",
    "thumbnail",
    "video_file",
    "audio_file",
    "voice_file",
    "sticker_file",
    "animation_file",
)


@dataclass(frozen=True)
class RenderedMessage:
    message_id: int | str
    time_text: str
    author: str
    text: str
    attachments: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a Telegram Desktop JSON export into date-based markdown notes."
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
        "--move-media",
        action="store_true",
        help="Move exported media files into the destination instead of copying them",
    )
    parser.add_argument(
        "--archive-export",
        action="store_true",
        help="Copy the original export folder into telegram/raw/<export-name>",
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    cleaned = re.sub(r"[-\s]+", "-", cleaned)
    return cleaned or "telegram-chat"


def parse_export_datetime(raw: str, timezone: ZoneInfo) -> datetime:
    normalized = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone)


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()

    if not isinstance(value, list):
        return ""

    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def extract_author(message: dict[str, Any]) -> str:
    author = message.get("from")
    if isinstance(author, str) and author.strip():
        return author.strip()

    actor = message.get("actor")
    if isinstance(actor, str) and actor.strip():
        return actor.strip()

    return "Unknown"


def find_attachment_paths(message: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ATTACHMENT_KEYS:
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())

    media_values = message.get("files")
    if isinstance(media_values, list):
        for item in media_values:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
            elif isinstance(item, dict):
                path = item.get("file")
                if isinstance(path, str) and path.strip():
                    paths.append(path.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def load_export_chats(export_dir: Path) -> list[dict[str, Any]]:
    result_path = export_dir / "result.json"
    if not result_path.exists():
        raise FileNotFoundError(f"result.json not found in {export_dir}")

    payload = json.loads(result_path.read_text(encoding="utf-8"))

    if isinstance(payload.get("messages"), list):
        return [payload]

    chats = payload.get("chats")
    if isinstance(chats, dict):
        chat_list = chats.get("list")
        if isinstance(chat_list, list):
            return [chat for chat in chat_list if isinstance(chat, dict)]
    if isinstance(chats, list):
        return [chat for chat in chats if isinstance(chat, dict)]

    raise ValueError("Unsupported Telegram export format: no messages or chats list found")


def chat_output_slug(chat: dict[str, Any]) -> str:
    name = str(chat.get("name") or "telegram-chat")
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        return slugify(name)

    normalized_id = re.sub(r"[^0-9A-Za-z_-]", "-", chat_id).strip("-")
    if not normalized_id:
        return slugify(name)
    return f"{slugify(name)}-{normalized_id}"


def copy_or_move_file(source: Path, destination: Path, move_media: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return

    if move_media:
        shutil.move(str(source), str(destination))
        return

    shutil.copy2(source, destination)


def relative_markdown_path(path: Path) -> str:
    return path.as_posix().replace(" ", "%20")


def render_attachment_line(relative_path: Path) -> str:
    target = relative_markdown_path(relative_path)
    filename = relative_path.name
    if relative_path.suffix.lower() in IMAGE_SUFFIXES:
        return f"![{filename}]({target})"
    return f"[{filename}]({target})"


def build_note(chat_name: str, export_name: str, date_key: str, messages: list[RenderedMessage]) -> str:
    lines = [
        f"# {chat_name} | {date_key}",
        "",
        f"_Source export: {export_name}_",
        "",
    ]

    for message in messages:
        lines.append(f"## {message.time_text} | {message.author}")
        lines.append(f"- message_id: `{message.message_id}`")
        if message.text:
            lines.append("")
            lines.append(message.text)
        if message.attachments:
            lines.append("")
            lines.append("Attachments:")
            for attachment in message.attachments:
                lines.append(f"- {attachment}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def archive_export_folder(export_dir: Path, output_root: Path) -> None:
    archive_root = output_root / "raw" / export_dir.name
    if archive_root.exists():
        return
    shutil.copytree(export_dir, archive_root)


def render_chat_messages(
    chat: dict[str, Any],
    export_dir: Path,
    output_root: Path,
    timezone: ZoneInfo,
    move_media: bool,
) -> dict[str, list[RenderedMessage]]:
    grouped: dict[str, list[tuple[datetime, RenderedMessage]]] = defaultdict(list)
    chat_name = str(chat.get("name") or "telegram-chat")
    chat_slug = chat_output_slug(chat)

    for message in chat.get("messages", []):
        if not isinstance(message, dict):
            continue
        if message.get("type") != "message":
            continue

        raw_date = message.get("date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            continue

        parsed_dt = parse_export_datetime(raw_date, timezone)
        date_key = parsed_dt.date().isoformat()
        attachment_links: list[str] = []

        for raw_attachment in find_attachment_paths(message):
            source = export_dir / raw_attachment
            if not source.exists():
                continue

            date_dir = output_root / "chats" / chat_slug / date_key[:4] / date_key[5:7]
            asset_dir = date_dir / f"{date_key}_assets"
            destination = asset_dir / f"{message.get('id', 'msg')}_{source.name}"
            copy_or_move_file(source, destination, move_media=move_media)
            relative_to_note = Path(f"./{date_key}_assets") / destination.name
            attachment_links.append(render_attachment_line(relative_to_note))

        rendered = RenderedMessage(
            message_id=message.get("id", ""),
            time_text=parsed_dt.strftime("%H:%M:%S"),
            author=extract_author(message),
            text=flatten_text(message.get("text")),
            attachments=attachment_links,
        )
        grouped[date_key].append((parsed_dt, rendered))

    output: dict[str, list[RenderedMessage]] = {}
    for date_key, items in grouped.items():
        ordered = [item for _, item in sorted(items, key=lambda pair: pair[0])]
        output[date_key] = ordered
    return output


def write_chat_notes(
    chat: dict[str, Any],
    export_dir: Path,
    output_root: Path,
    rendered_days: dict[str, list[RenderedMessage]],
) -> int:
    chat_name = str(chat.get("name") or "telegram-chat")
    chat_slug = chat_output_slug(chat)
    note_count = 0

    for date_key, messages in sorted(rendered_days.items()):
        year = date_key[:4]
        month = date_key[5:7]
        note_dir = output_root / "chats" / chat_slug / year / month
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{date_key}.md"
        note_content = build_note(chat_name, export_dir.name, date_key, messages)
        note_path.write_text(note_content, encoding="utf-8")
        manifest_path = manifest_path_for_note(note_path)
        for message in messages:
            attachments = []
            for attachment in message.attachments:
                if "(" in attachment and ")" in attachment:
                    relative_target = attachment.split("(", 1)[-1].rstrip(")")
                    attachments.append(str((note_path.parent / relative_target).resolve()))
            append_jsonl_record(
                manifest_path,
                RawManifestRecord(
                    source_type="telegram_export",
                    source_path=str(note_path.resolve()),
                    doc_id=f"export:{chat_slug}:{message.message_id}",
                    date=date_key,
                    sender=message.author,
                    attachments=attachments,
                    hash=sha256_text(str(message.message_id), message.author, message.text, "".join(message.attachments)),
                ),
                dedupe_key="doc_id",
            )
        note_count += 1

    return note_count


def main() -> None:
    args = parse_args()
    export_dir = args.export_dir.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    timezone = ZoneInfo(args.timezone)

    chats = load_export_chats(export_dir)

    if args.archive_export:
        archive_export_folder(export_dir, output_root)

    chat_count = 0
    note_count = 0
    for chat in chats:
        rendered_days = render_chat_messages(
            chat=chat,
            export_dir=export_dir,
            output_root=output_root,
            timezone=timezone,
            move_media=args.move_media,
        )
        if not rendered_days:
            continue
        note_count += write_chat_notes(
            chat=chat,
            export_dir=export_dir,
            output_root=output_root,
            rendered_days=rendered_days,
        )
        chat_count += 1

    print(
        f"Imported {chat_count} chat(s) and wrote {note_count} day note(s) into {output_root}"
    )


if __name__ == "__main__":
    main()
