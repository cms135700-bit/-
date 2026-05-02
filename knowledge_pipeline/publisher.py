from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .indexer import reindex_vault
from .verifier import verify_merge


SAFE_MERGE_RECOMMENDATIONS = {
    "append_with_new_section",
    "create_new_note_or_section",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_block(items: list[str]) -> str:
    if not items:
        return "- 없음"
    return "\n".join(f"- {item}" for item in items)


def source_block(source_units: list[str]) -> str:
    return "\n".join(f"- `{item}`" for item in source_units) if source_units else "- 없음"


def render_insight_brief(artifact: dict[str, Any], spec: dict[str, Any]) -> str:
    lines = [
        f"## {spec['section_title']}",
        "",
        "> [!info] Pipeline Insight",
        f"> {artifact.get('core_mechanism', '')}",
        "",
        "### Connection Map",
        list_block(artifact.get("connection_map", [])),
        "",
        "### Investment Implications",
        list_block(artifact.get("investment_implications", [])),
        "",
        "### Confidence Flags",
        list_block(artifact.get("confidence_flags", [])),
        "",
        "### Backlinks",
        list_block(spec.get("backlinks", [])),
        "",
        "### Sources",
        source_block(spec.get("source_block", [])),
    ]
    return "\n".join(lines).strip() + "\n"


def render_thesis_card(artifact: dict[str, Any], spec: dict[str, Any]) -> str:
    verification_units = artifact.get("verification_units", [])
    verification_lines = [
        f"- {unit.get('label', '')}: {unit.get('claim', '')}".strip(": ")
        for unit in verification_units
        if unit.get("claim")
    ]
    number_lines = [
        f"- {fact.get('value', '')}: {fact.get('context', '')}".strip(": ")
        for fact in artifact.get("numbers", [])
        if fact.get("value") and fact.get("context")
    ]
    lines = [
        f"## {spec['section_title']}",
        "",
        "> [!info] 리포트 논리 해부",
        f"> 핵심 주장: {artifact.get('core_claim', '')}",
        "",
        "### 최소 검증 단위",
        list_block(verification_lines),
        "",
        "### Narrative",
        list_block(artifact.get("narrative", [])),
        "",
        "### Numbers",
        list_block(number_lines),
        "",
        "### Open Questions",
        list_block(artifact.get("open_questions", [])),
        "",
        "### Backlinks",
        list_block(spec.get("backlinks", [])),
        "",
        "### Sources",
        source_block(spec.get("source_block", [])),
    ]
    return "\n".join(lines).strip() + "\n"


def render_evidence_packet(artifact: dict[str, Any], spec: dict[str, Any]) -> str:
    units = artifact.get("units", [])
    lines = [
        f"## {spec['section_title']}",
        "",
        "> [!note] Evidence Packet",
        f"> 텔레그램 raw를 provenance 유지 형태로 정규화한 산출물이다. 단위 수: {len(units)}",
        "",
        "### Backlinks",
        list_block(spec.get("backlinks", [])),
        "",
        "### Sources",
        source_block(spec.get("source_block", [])),
    ]
    return "\n".join(lines).strip() + "\n"


def render_section(artifact: dict[str, Any], spec: dict[str, Any]) -> str:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "insight_brief":
        return render_insight_brief(artifact, spec)
    if artifact_type == "thesis_card":
        return render_thesis_card(artifact, spec)
    if artifact_type == "evidence_packet":
        return render_evidence_packet(artifact, spec)
    raise ValueError(f"unsupported artifact_type for publish: {artifact_type}")


def initial_note_header(destination_path: Path) -> str:
    title = destination_path.stem
    return f"# {title}\n\n"


def target_requires_verification(target: dict[str, Any], destination_path: Path) -> bool:
    return target.get("destination_type") in {"company_note", "industry_note", "macro_note"} and destination_path.exists()


def assert_verification_ok(
    verification_report_path: Path | None,
    destination_path: Path,
) -> None:
    if verification_report_path is None:
        raise ValueError("existing company/industry notes require verification_report before publish")
    report = load_json(verification_report_path)
    if Path(report.get("target_path", "")).resolve() != destination_path.resolve():
        raise ValueError("verification_report target does not match publish destination")
    recommendation = report.get("merge_recommendation")
    if recommendation not in SAFE_MERGE_RECOMMENDATIONS:
        raise ValueError(f"publish blocked by verification_report: {recommendation}")


def primary_target_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if "primary_target" in spec:
        return spec["primary_target"]
    return {
        "destination_type": spec.get("destination_type"),
        "destination_path": spec.get("destination_path"),
        "update_mode": spec.get("update_mode"),
        "section_title": spec.get("section_title"),
        "backlinks": spec.get("backlinks", []),
        "source_block": spec.get("source_block", []),
        "role": "primary",
    }


def related_targets_from_spec(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return list(spec.get("related_targets", []))


def already_published(note_text: str, spec: dict[str, Any]) -> bool:
    section_title = spec.get("section_title", "")
    source_refs = spec.get("source_block", [])
    if section_title and f"## {section_title}" in note_text:
        return True
    if source_refs and all(ref in note_text for ref in source_refs[: min(2, len(source_refs))]):
        return True
    return False


def publish_to_target(
    artifact: dict[str, Any],
    artifact_path: Path,
    target: dict[str, Any],
    *,
    verification_report_path: Path | None = None,
    require_explicit_verification: bool,
) -> tuple[Path, str]:
    destination_path = Path(target["destination_path"])
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    if target_requires_verification(target, destination_path):
        if require_explicit_verification:
            assert_verification_ok(verification_report_path, destination_path)
        else:
            report = verify_merge(destination_path, artifact_path)
            recommendation = report.merge_recommendation
            if recommendation == "skip_existing_source":
                return destination_path, "skipped_duplicate"
            if recommendation not in SAFE_MERGE_RECOMMENDATIONS:
                return destination_path, f"skipped_{recommendation}"

    section = render_section(artifact, target)
    if destination_path.exists():
        note_text = destination_path.read_text(encoding="utf-8")
        if already_published(note_text, target):
            return destination_path, "skipped_duplicate"
        destination_path.write_text(note_text.rstrip() + "\n\n" + section, encoding="utf-8")
        return destination_path, "appended"

    destination_path.write_text(initial_note_header(destination_path) + section, encoding="utf-8")
    return destination_path, "created"


def append_publish_log(
    *,
    artifact: dict[str, Any],
    spec: dict[str, Any],
    log_path: Path,
    primary_target: dict[str, Any],
    published_paths: list[Path],
    skipped_targets: list[str],
) -> None:
    date_label = artifact.get("effective_date")
    if not date_label:
        generated_at = artifact.get("generated_at", "")
        if isinstance(generated_at, str) and generated_at[:10]:
            date_label = generated_at[:10]
    if not date_label:
        date_label = datetime.now().date().isoformat()

    summary = spec.get("log_summary") or artifact.get("title") or Path(primary_target["destination_path"]).stem
    lines = [
        f"## [{date_label}] publish | {summary}",
        "",
        f"- primary: `{primary_target['destination_path']}`",
    ]
    if published_paths:
        lines.append("- published:")
        lines.extend(f"  - `{path}`" for path in published_paths)
    if skipped_targets:
        lines.append("- skipped:")
        lines.extend(f"  - {item}" for item in skipped_targets)
    sources = primary_target.get("source_block", [])
    if sources:
        lines.append("- sources:")
        lines.extend(f"  - `{source}`" for source in sources[:6])
    entry = "\n".join(lines).rstrip() + "\n\n"
    current = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    log_path.write_text(current.rstrip() + "\n\n" + entry if current.strip() else entry, encoding="utf-8")


def publish_artifact(
    artifact_path: Path,
    spec_path: Path,
    verification_report_path: Path | None = None,
) -> Path:
    artifact = load_json(artifact_path)
    spec = load_json(spec_path)
    primary_target = primary_target_from_spec(spec)
    related_targets = related_targets_from_spec(spec)

    primary_path, _ = publish_to_target(
        artifact,
        artifact_path,
        primary_target,
        verification_report_path=verification_report_path,
        require_explicit_verification=True,
    )

    published_paths = [primary_path]
    skipped_targets: list[str] = []
    for target in related_targets:
        target_path, status = publish_to_target(
            artifact,
            artifact_path,
            target,
            verification_report_path=None,
            require_explicit_verification=False,
        )
        if status in {"created", "appended"}:
            published_paths.append(target_path)
        else:
            skipped_targets.append(f"{target_path}: {status}")

    vault_root = spec.get("vault_root")
    if spec.get("update_index", True) and vault_root:
        index_path, log_path = reindex_vault(Path(vault_root))
    else:
        index_path = primary_path
        log_path = Path(vault_root) / "log.md" if vault_root else None  # type: ignore[assignment]

    if spec.get("append_log", True) and vault_root:
        assert log_path is not None
        append_publish_log(
            artifact=artifact,
            spec=spec,
            log_path=log_path,
            primary_target=primary_target,
            published_paths=published_paths,
            skipped_targets=skipped_targets,
        )

    if spec.get("update_index", True) and vault_root:
        reindex_vault(Path(vault_root))

    return primary_path
