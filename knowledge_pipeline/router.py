from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .schemas import PublishSpec, PublishTarget
from .utils import first_nonempty, write_json


INDUSTRY_TOPIC_DIR = {
    "반도체": "산업/반도체",
    "광통신/CPO": "산업/광통신",
    "태양광/에너지": "산업/발전",
    "전력/변압기": "산업/발전",
    "AI/데이터센터": "산업/반도체",
    "바이오/헬스케어": "산업/미분류",
    "주주환원/밸류업": "매크로",
    "수출데이터": "매크로",
    "매크로/지정학": "매크로",
}


def normalize_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def existing_company_map(vault_root: Path) -> dict[str, Path]:
    company_dir = vault_root / "기업"
    mapping: dict[str, Path] = {}
    if not company_dir.exists():
        return mapping
    for note in company_dir.glob("*.md"):
        mapping[normalize_name(note.stem)] = note.resolve()
    return mapping


def existing_note_map(vault_root: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for root_name in ("기업", "산업", "매크로"):
        note_root = vault_root / root_name
        if not note_root.exists():
            continue
        for note in note_root.rglob("*.md"):
            keys = {
                normalize_name(note.stem),
                normalize_name(str(note.relative_to(vault_root).with_suffix(""))),
            }
            for key in keys:
                if key:
                    mapping[key] = note.resolve()
    return mapping


def load_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_names(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "thesis_card":
        return artifact.get("ticker_theme_candidates", [])
    if artifact_type == "insight_brief":
        return artifact.get("primary_topics", [])
    return []


def source_block(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "thesis_card":
        return [value for value in [artifact.get("source_path"), artifact.get("doc_id")] if value]
    return artifact.get("source_units", [])


def find_company_destination(artifact: dict[str, Any], vault_root: Path) -> Path | None:
    company_map = existing_company_map(vault_root)
    for candidate in candidate_names(artifact):
        note = company_map.get(normalize_name(candidate))
        if note:
            return note
    title = artifact.get("title", "")
    for key, note in company_map.items():
        if key and key in normalize_name(title):
            return note
    return None


def determine_destination(artifact: dict[str, Any], vault_root: Path) -> tuple[str, Path, str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "insight_brief":
        date = artifact.get("effective_date")
        if date:
            path = vault_root / "인사이트 & 뉴스" / f"{date} 인사이트.md"
            return "daily_insight_note", path, "append_section"

    company_path = find_company_destination(artifact, vault_root)
    if company_path:
        return "company_note", company_path, "append_section"

    primary_topic = first_nonempty(candidate_names(artifact))
    industry_dir = INDUSTRY_TOPIC_DIR.get(primary_topic, "산업/미분류")
    title = artifact.get("title") or primary_topic or "untitled"
    note_name = f"{title}".replace("/", "_")
    path = vault_root / industry_dir / f"{note_name}.md"
    return "industry_note", path, "create_or_append"


def build_backlinks(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "insight_brief":
        seeds = artifact.get("primary_topics", []) + artifact.get("related_notes", [])
    else:
        seeds = artifact.get("ticker_theme_candidates", [])
    backlinks: list[str] = []
    seen: set[str] = set()
    for seed in seeds:
        if not seed:
            continue
        link = seed if seed.startswith("[[") else f"[[{seed}]]"
        if link in seen:
            continue
        seen.add(link)
        backlinks.append(link)
    return backlinks[:8]


def unpack_seed_terms(seed: str) -> list[str]:
    normalized = seed.strip()
    if not normalized:
        return []
    if normalized.startswith("[[") and normalized.endswith("]]"):
        normalized = normalized[2:-2]
    candidates = [normalized]
    if "|" in normalized:
        left, right = normalized.split("|", 1)
        candidates.extend([left, right])
        normalized = left
    if "/" in normalized:
        candidates.extend(part for part in normalized.split("/") if part)
    if "\\" in normalized:
        candidates.extend(part for part in normalized.split("\\") if part)

    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_name(candidate)
        if key and key not in seen:
            seen.add(key)
            terms.append(key)
    return terms


def candidate_related_paths(artifact: dict[str, Any], vault_root: Path) -> list[Path]:
    note_map = existing_note_map(vault_root)
    seeds = candidate_names(artifact)
    if artifact.get("artifact_type") == "insight_brief":
        seeds = seeds + artifact.get("related_notes", [])

    paths: list[Path] = []
    seen: set[Path] = set()
    for seed in seeds:
        for term in unpack_seed_terms(seed):
            note = note_map.get(term)
            if note and note not in seen:
                seen.add(note)
                paths.append(note)
    return paths


def build_target(
    *,
    destination_type: str,
    destination_path: Path,
    update_mode: str,
    section_title: str,
    backlinks: list[str],
    source_units: list[str],
    role: str,
) -> PublishTarget:
    return PublishTarget(
        destination_type=destination_type,
        destination_path=str(destination_path.resolve()),
        update_mode=update_mode,
        section_title=section_title,
        backlinks=backlinks,
        source_block=source_units,
        role=role,
    )


def route_artifact(artifact_path: Path, vault_root: Path) -> PublishSpec:
    vault_root = vault_root.resolve()
    artifact = load_artifact(artifact_path)
    destination_type, destination_path, update_mode = determine_destination(artifact, vault_root)
    title = artifact.get("title", destination_path.stem)
    section_title = first_nonempty(
        [
            f"{artifact.get('effective_date')} 업데이트" if artifact.get("effective_date") else "",
            title,
        ]
    )
    backlinks = build_backlinks(artifact)
    sources = source_block(artifact)
    primary_target = build_target(
        destination_type=destination_type,
        destination_path=destination_path,
        update_mode=update_mode,
        section_title=section_title,
        backlinks=backlinks,
        source_units=sources,
        role="primary",
    )

    related_targets: list[PublishTarget] = []
    for related_path in candidate_related_paths(artifact, vault_root):
        if related_path.resolve() == destination_path.resolve():
            continue
        relative = related_path.relative_to(vault_root)
        top_level = relative.parts[0] if relative.parts else ""
        related_type = {
            "기업": "company_note",
            "산업": "industry_note",
            "매크로": "macro_note",
        }.get(top_level, "related_note")
        related_targets.append(
            build_target(
                destination_type=related_type,
                destination_path=related_path,
                update_mode="append_section",
                section_title=section_title,
                backlinks=backlinks,
                source_units=sources,
                role="related",
            )
        )

    return PublishSpec(
        primary_target=primary_target,
        related_targets=related_targets,
        vault_root=str(vault_root.resolve()),
        update_index=True,
        append_log=True,
        log_summary=title,
        reason=f"{artifact.get('artifact_type')}를 규칙 기반으로 라우팅한 결과",
        destination_type=primary_target.destination_type,
        destination_path=primary_target.destination_path,
        update_mode=primary_target.update_mode,
        section_title=primary_target.section_title,
        backlinks=primary_target.backlinks,
        source_block=primary_target.source_block,
    )


def route_to_spec(artifact_path: Path, vault_root: Path, output_path: Path | None = None) -> Path:
    spec = route_artifact(artifact_path, vault_root)
    destination = output_path or artifact_path.with_suffix(".publish_spec.json")
    write_json(destination, spec)
    return destination
