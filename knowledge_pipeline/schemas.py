from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .utils import iso_now


@dataclass
class RawManifestRecord:
    source_type: str
    source_path: str
    date: str
    sender: str
    hash: str
    message_key: str | None = None
    doc_id: str | None = None
    forwarded_from: str | None = None
    attachments: list[str] = field(default_factory=list)
    artifact_type: Literal["raw_manifest"] = "raw_manifest"
    generated_at: str = field(default_factory=iso_now)


@dataclass
class EvidenceUnit:
    unit_id: str
    source_refs: list[str]
    verbatim_core: list[str]
    ocr_text: list[str] = field(default_factory=list)
    extracted_numbers: list[str] = field(default_factory=list)
    image_read_limits: list[str] = field(default_factory=list)
    topic_candidates: list[str] = field(default_factory=list)
    sender: str | None = None
    forwarded_from: str | None = None


@dataclass
class EvidencePacket:
    source_path: str
    date: str
    units: list[EvidenceUnit]
    source_type: Literal["telegram_day"] = "telegram_day"
    artifact_type: Literal["evidence_packet"] = "evidence_packet"
    generated_at: str = field(default_factory=iso_now)


@dataclass
class VerificationUnit:
    label: str
    claim: str
    evidence: list[str] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    weakness: str = ""


@dataclass
class NumberFact:
    value: str
    context: str
    page_ref: int | None = None


@dataclass
class ThesisCard:
    doc_id: str
    title: str
    source_path: str
    core_claim: str
    verification_units: list[VerificationUnit]
    narrative: list[str]
    numbers: list[NumberFact]
    page_refs: list[int]
    ticker_theme_candidates: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    artifact_type: Literal["thesis_card"] = "thesis_card"
    generated_at: str = field(default_factory=iso_now)


@dataclass
class InsightBrief:
    title: str
    core_mechanism: str
    connection_map: list[str]
    investment_implications: list[str]
    source_units: list[str]
    related_notes: list[str]
    confidence_flags: list[str]
    primary_topics: list[str] = field(default_factory=list)
    effective_date: str | None = None
    artifact_type: Literal["insight_brief"] = "insight_brief"
    generated_at: str = field(default_factory=iso_now)


@dataclass
class PublishTarget:
    destination_type: str
    destination_path: str
    update_mode: str
    section_title: str
    backlinks: list[str]
    source_block: list[str]
    role: str = "related"


@dataclass
class PublishSpec:
    primary_target: PublishTarget
    reason: str
    related_targets: list[PublishTarget] = field(default_factory=list)
    vault_root: str = ""
    update_index: bool = True
    append_log: bool = True
    log_summary: str = ""
    destination_type: str | None = None
    destination_path: str | None = None
    update_mode: str | None = None
    section_title: str | None = None
    backlinks: list[str] = field(default_factory=list)
    source_block: list[str] = field(default_factory=list)
    artifact_type: Literal["publish_spec"] = "publish_spec"
    generated_at: str = field(default_factory=iso_now)


@dataclass
class VerificationReport:
    target_path: str
    duplicates: list[str]
    contradictions: list[str]
    missing_sources: list[str]
    stale_claims: list[str]
    merge_recommendation: str
    artifact_type: Literal["verification_report"] = "verification_report"
    generated_at: str = field(default_factory=iso_now)
