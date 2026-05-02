from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .schemas import VerificationReport
from .utils import clean_whitespace, extract_numbers, write_json


DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def load_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_source_refs(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "publish_spec":
        primary_target = artifact.get("primary_target", {})
        return primary_target.get("source_block", artifact.get("source_block", []))
    if artifact_type == "insight_brief":
        return artifact.get("source_units", [])
    if artifact_type == "thesis_card":
        return [value for value in [artifact.get("source_path"), artifact.get("doc_id")] if value]
    return []


def artifact_number_facts(artifact: dict[str, Any]) -> list[tuple[str, str]]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "thesis_card":
        facts = []
        for fact in artifact.get("numbers", []):
            value = fact.get("value", "")
            context = fact.get("context", "")
            if value and context:
                facts.append((value, context))
        return facts
    return []


def latest_note_date(note_text: str) -> str | None:
    dates = DATE_RE.findall(note_text)
    if not dates:
        return None
    return max(dates)


def context_signature(text: str) -> str:
    text = re.sub(r"\d[\d,./%-]*", "", text)
    text = re.sub(r"[^\w\s가-힣]", " ", text, flags=re.UNICODE)
    words = [word for word in text.lower().split() if len(word) > 1]
    return " ".join(words[:8])


def find_contradictions(note_text: str, artifact: dict[str, Any]) -> list[str]:
    contradictions: list[str] = []
    note_lines = [line.strip() for line in note_text.splitlines() if line.strip()]
    for value, context in artifact_number_facts(artifact):
        signature = context_signature(context)
        if not signature:
            continue
        for line in note_lines:
            if signature and signature in context_signature(line):
                line_numbers = extract_numbers(line, limit=6)
                if line_numbers and value not in line_numbers:
                    contradictions.append(
                        f"기존 노트의 `{line[:120]}` 와 새 근거 `{context[:120]}` 사이에 숫자 차이가 있음"
                    )
                    break
    return contradictions[:6]


def verify_merge(note_path: Path, artifact_path: Path) -> VerificationReport:
    artifact = load_artifact(artifact_path)
    note_text = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
    refs = artifact_source_refs(artifact)
    duplicates = [ref for ref in refs if ref and ref in note_text]
    contradictions = find_contradictions(note_text, artifact) if note_text else []
    missing_sources = ["artifact에 source reference가 없음"] if not refs else []
    stale_claims: list[str] = []

    artifact_date = artifact.get("effective_date")
    note_date = latest_note_date(note_text)
    if artifact_date and note_date and artifact_date > note_date:
        stale_claims.append(f"기존 노트의 최신 날짜 `{note_date}` 보다 새 artifact 날짜 `{artifact_date}` 가 더 최신임")

    if duplicates and not contradictions:
        recommendation = "skip_existing_source"
    elif contradictions:
        recommendation = "manual_review_before_merge"
    elif not note_text:
        recommendation = "create_new_note_or_section"
    else:
        recommendation = "append_with_new_section"

    return VerificationReport(
        target_path=str(note_path.resolve()),
        duplicates=duplicates,
        contradictions=contradictions,
        missing_sources=missing_sources,
        stale_claims=stale_claims,
        merge_recommendation=recommendation,
    )


def write_verification(note_path: Path, artifact_path: Path, output_path: Path | None = None) -> Path:
    report = verify_merge(note_path, artifact_path)
    destination = output_path or artifact_path.with_suffix(".verification_report.json")
    write_json(destination, report)
    return destination
