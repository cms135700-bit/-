from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


GENERIC_FILENAME_TOKENS = {
    "report",
    "company",
    "industry",
    "market",
    "pdf",
    "update",
    "review",
    "preview",
    "buy",
    "hold",
    "outperform",
    "neutral",
    "research",
    "리포트",
    "산업",
    "기업",
    "코멘트",
    "업종",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return to_plain_data(asdict(value))
    if isinstance(value, dict):
        return {key: to_plain_data(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_plain_data(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl_record(path: Path, record: Any, dedupe_key: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = to_plain_data(record)
    if dedupe_key:
        existing = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get(dedupe_key):
                    existing.add(str(payload[dedupe_key]))
        key_value = plain.get(dedupe_key)
        if key_value is not None and str(key_value) in existing:
            return

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(plain, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def sha256_text(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    cleaned = re.sub(r"[-\s]+", "-", cleaned)
    return cleaned or "untitled"


def clean_whitespace(text: str) -> str:
    text = text.replace("\u200b", "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cleaned_lines(text: str) -> list[str]:
    return [line.strip() for line in clean_whitespace(text).splitlines() if line.strip()]


def extract_numbers(text: str, *, limit: int = 20) -> list[str]:
    pattern = re.compile(
        r"""
        (?:
            [+-]?\d[\d,]*(?:\.\d+)?% |
            [+-]?\d[\d,]*(?:\.\d+)?(?:조|억|만|천)?(?:원|달러|위안|GW|MW|kW|GB|TB|x|배|bp|%)? |
            \$\d[\d,]*(?:\.\d+)?(?:[BMK])?
        )
        """,
        re.VERBOSE,
    )
    values: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        value = match.group(0).strip()
        if len(value) < 2 or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def infer_filename_candidates(stem: str, *, limit: int = 6) -> list[str]:
    normalized = stem.replace("_", " ").replace("-", " ")
    tokens = [token.strip() for token in re.split(r"\s+", normalized) if token.strip()]
    candidates: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        if lowered in GENERIC_FILENAME_TOKENS:
            continue
        if lowered.isdigit() or re.fullmatch(r"\d{6,8}", lowered):
            continue
        if re.fullmatch(r"\d+(?:f|e)?", lowered):
            continue
        if len(token) <= 1:
            continue
        if token in seen:
            continue
        seen.add(token)
        candidates.append(token)
        if len(candidates) >= limit:
            break
    return candidates


def top_items(items: Iterable[str], *, limit: int = 5) -> list[str]:
    counter = Counter(item for item in items if item)
    return [item for item, _ in counter.most_common(limit)]


def first_nonempty(items: Iterable[str]) -> str:
    for item in items:
        if item and item.strip():
            return item.strip()
    return ""
