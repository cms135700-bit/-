from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .schemas import EvidencePacket, EvidenceUnit, RawManifestRecord
from .utils import (
    append_jsonl_record,
    clean_whitespace,
    cleaned_lines,
    extract_numbers,
    first_nonempty,
    sha256_text,
    write_json,
)


SECTION_SPLIT_RE = re.compile(r"(?m)^## ")
ATTACHMENT_TARGET_RE = re.compile(r"\(([^)]+)\)")


@dataclass
class ParsedTelegramMessage:
    timestamp: str
    sender: str
    message_key: str
    forwarded_from: str
    topics: list[str]
    body_text: str
    attachments: list[str]
    note_path: Path


def manifest_path_for_note(note_path: Path) -> Path:
    normalized = note_path.resolve()
    parts = list(normalized.parts)
    if "telegram" not in parts:
        return normalized.with_suffix(".raw_manifest.jsonl")
    telegram_index = parts.index("telegram")
    tail = parts[telegram_index + 1 :]
    if not tail:
        return normalized.with_suffix(".raw_manifest.jsonl")
    if tail[0] in {"bot", "chats"} and len(tail) >= 4:
        _, slug, year, month, filename = tail[:5]
        manifest_name = filename.replace(".md", ".raw_manifest.jsonl")
        return Path(*parts[: telegram_index + 1]) / "manifests" / slug / year / month / manifest_name
    return normalized.with_suffix(".raw_manifest.jsonl")


def evidence_path_for_note(note_path: Path) -> Path:
    normalized = note_path.resolve()
    parts = list(normalized.parts)
    if "telegram" not in parts:
        return normalized.with_suffix(".evidence_packet.json")
    telegram_index = parts.index("telegram")
    tail = parts[telegram_index + 1 :]
    if tail and tail[0] in {"bot", "chats"} and len(tail) >= 5:
        _, slug, year, month, filename = tail[:5]
        evidence_name = filename.replace(".md", ".evidence_packet.json")
        return Path(*parts[: telegram_index + 1]) / "normalized" / slug / year / month / evidence_name
    return normalized.with_suffix(".evidence_packet.json")


def parse_attachment_target(line: str, note_path: Path) -> str:
    match = ATTACHMENT_TARGET_RE.search(line)
    if not match:
        return line.removeprefix("- ").strip()
    target = match.group(1).strip()
    return str((note_path.parent / target).resolve())


def split_sections(note_text: str) -> list[str]:
    parts = SECTION_SPLIT_RE.split(note_text)
    return [part for part in parts[1:] if part.strip()]


def parse_metadata_line(line: str) -> tuple[str, str] | None:
    if not line.startswith("- "):
        return None
    payload = line[2:].strip()
    if ":" not in payload:
        return None
    key, value = payload.split(":", 1)
    return key.strip(), value.strip().strip("`")


def parse_telegram_note(note_path: Path) -> list[ParsedTelegramMessage]:
    note_text = note_path.read_text(encoding="utf-8")
    messages: list[ParsedTelegramMessage] = []

    for section in split_sections(note_text):
        lines = section.splitlines()
        if not lines:
            continue
        header = lines[0].strip()
        if "|" not in header:
            continue
        timestamp, sender = [part.strip() for part in header.split("|", 1)]

        metadata: dict[str, str] = {}
        body_lines: list[str] = []
        attachments: list[str] = []
        in_original = False
        in_attachments = False

        for raw_line in lines[1:]:
            line = raw_line.rstrip()
            stripped = line.strip()

            if stripped == "원문:":
                in_original = True
                in_attachments = False
                continue
            if stripped == "Attachments:":
                in_original = False
                in_attachments = True
                continue

            if in_attachments:
                if stripped.startswith("- "):
                    attachments.append(parse_attachment_target(stripped, note_path))
                continue

            if not in_original:
                parsed = parse_metadata_line(stripped)
                if parsed:
                    key, value = parsed
                    metadata[key] = value
                    continue

            body_lines.append(line)

        message_key = metadata.get("message_key", "")
        if not message_key:
            continue

        topics = [
            topic.strip()
            for topic in metadata.get("항목", "").split(",")
            if topic.strip()
        ]
        messages.append(
            ParsedTelegramMessage(
                timestamp=timestamp,
                sender=sender,
                message_key=message_key,
                forwarded_from=metadata.get("forwarded_from", ""),
                topics=topics,
                body_text=clean_whitespace("\n".join(body_lines)),
                attachments=attachments,
                note_path=note_path.resolve(),
            )
        )

    return messages


def build_manifest_record(message: ParsedTelegramMessage) -> RawManifestRecord:
    digest = sha256_text(
        message.message_key,
        message.sender,
        message.forwarded_from,
        message.body_text,
        "".join(message.attachments),
    )
    return RawManifestRecord(
        source_type="telegram_note",
        source_path=str(message.note_path),
        message_key=message.message_key,
        date=message.note_path.stem,
        sender=message.sender,
        forwarded_from=message.forwarded_from or None,
        attachments=message.attachments,
        hash=digest,
    )


def write_manifest_records(note_path: Path, messages: list[ParsedTelegramMessage]) -> Path:
    manifest_path = manifest_path_for_note(note_path)
    for message in messages:
        append_jsonl_record(
            manifest_path,
            build_manifest_record(message),
            dedupe_key="message_key",
        )
    return manifest_path


def build_evidence_packet(note_path: Path) -> EvidencePacket:
    messages = parse_telegram_note(note_path)
    units: list[EvidenceUnit] = []
    for message in messages:
        source_refs = [str(message.note_path), message.message_key, *message.attachments]
        core_lines = cleaned_lines(message.body_text)[:6]
        limits: list[str] = []
        if message.attachments:
            for attachment in message.attachments:
                suffix = Path(attachment).suffix.lower()
                if suffix == ".pdf":
                    limits.append(f"첨부 PDF 본문 OCR/시각 해독 미수행: {Path(attachment).name}")
                elif suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                    limits.append(f"첨부 이미지 OCR 미수행: {Path(attachment).name}")
                else:
                    limits.append(f"첨부 원문 직접 해독 미수행: {Path(attachment).name}")
        units.append(
            EvidenceUnit(
                unit_id=message.message_key,
                source_refs=source_refs,
                verbatim_core=core_lines or [first_nonempty([message.body_text, message.forwarded_from, message.sender])],
                ocr_text=[],
                extracted_numbers=extract_numbers(message.body_text),
                image_read_limits=limits,
                topic_candidates=message.topics,
                sender=message.sender,
                forwarded_from=message.forwarded_from or None,
            )
        )

    return EvidencePacket(
        source_path=str(note_path.resolve()),
        date=note_path.stem,
        units=units,
    )


def normalize_note_to_packet(note_path: Path, output_path: Path | None = None) -> Path:
    packet = build_evidence_packet(note_path)
    destination = output_path or evidence_path_for_note(note_path)
    write_json(destination, packet)
    return destination
