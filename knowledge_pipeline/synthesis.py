from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .schemas import InsightBrief
from .utils import first_nonempty, top_items, write_json


TOPIC_RULES = {
    "반도체": "반도체는 가격(ASP), 출하, Capex, 패키징 병목을 분리해서 봐야 한다.",
    "광통신/CPO": "광통신/CPO는 실제 양산 시점과 고객 Capex 전개 속도가 확인돼야 의미가 커진다.",
    "태양광/에너지": "에너지 재료는 정책, 증설, 프로젝트 발주, 원가 변수 중 어느 축인지 구분해야 한다.",
    "전력/변압기": "전력/변압기는 설치 지연과 공급 병목이 profit pool로 이어지는지 먼저 봐야 한다.",
    "AI/데이터센터": "AI는 칩보다 랙, 전력, 냉각, 계통 같은 물리 병목으로 profit pool이 내려오는지 확인해야 한다.",
    "바이오/헬스케어": "바이오/헬스케어는 플랫폼 서사보다 임상, 허가, 상업화 슬롯이 더 직접적인 검증 단위다.",
    "매크로/지정학": "매크로/지정학은 실적보다 먼저 할인율과 리스크 프리미엄에 반영된다는 점을 분리해서 봐야 한다.",
}


def load_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_topics(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "evidence_packet":
        topics: list[str] = []
        for unit in artifact.get("units", []):
            topics.extend(unit.get("topic_candidates", []))
        return topics
    if artifact_type == "thesis_card":
        return artifact.get("ticker_theme_candidates", [])
    return artifact.get("primary_topics", [])


def collect_source_units(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "evidence_packet":
        units = []
        for unit in artifact.get("units", []):
            refs = unit.get("source_refs", [])
            if refs:
                units.extend(refs[:2])
        return units
    if artifact_type == "thesis_card":
        values = [artifact.get("source_path", ""), artifact.get("doc_id", "")]
        return [value for value in values if value]
    return artifact.get("source_units", [])


def collect_claims(artifact: dict[str, Any]) -> list[str]:
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "evidence_packet":
        claims: list[str] = []
        for unit in artifact.get("units", []):
            claims.extend(unit.get("verbatim_core", [])[:2])
        return claims
    if artifact_type == "thesis_card":
        claims = [artifact.get("core_claim", "")]
        for unit in artifact.get("verification_units", []):
            claims.append(unit.get("claim", ""))
        return [claim for claim in claims if claim]
    return []


def collect_dates(artifact: dict[str, Any]) -> list[str]:
    values = []
    for key in ("date", "effective_date"):
        value = artifact.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            values.append(value)
    return values


def build_core_mechanism(topics: list[str], claims: list[str]) -> str:
    topic_summary = ", ".join(topics[:4]) if topics else "복수 주제"
    claim_summary = " / ".join(claims[:2]) if claims else "공통 주장 추출이 추가로 필요함"
    return f"공통 축은 {topic_summary}이며, 입력 소스들은 같은 병목/수익풀 변화를 반복적으로 지시한다. 대표 근거는 {claim_summary}."


def build_connection_map(topics: list[str], claims: list[str]) -> list[str]:
    output: list[str] = []
    if topics:
        output.append(f"반복 등장 주제: {', '.join(topics[:5])}")
    for claim in claims[:4]:
        output.append(claim[:220])
    return output[:6]


def build_investment_implications(topics: list[str]) -> list[str]:
    implications: list[str] = []
    for topic in topics:
        insight = TOPIC_RULES.get(topic)
        if insight and insight not in implications:
            implications.append(insight)
    if not implications:
        implications.append("공통 인사이트는 존재하지만, 투자 함의는 사람이 한 번 더 좁혀야 한다.")
    return implications[:5]


def build_related_notes(topics: list[str], source_units: list[str]) -> list[str]:
    note_links = [f"[[{topic}]]" for topic in topics[:5]]
    if any("telegram" in ref for ref in source_units):
        note_links.append("[[인사이트 & 뉴스]]")
    return list(dict.fromkeys(note_links))


def build_confidence_flags(artifacts: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for artifact in artifacts:
        if artifact.get("artifact_type") == "evidence_packet":
            if any(unit.get("image_read_limits") for unit in artifact.get("units", [])):
                flags.append("이미지/PDF 첨부 중 OCR 미수행 항목이 있어 원문 복원 완결성이 제한될 수 있음")
        if artifact.get("artifact_type") == "thesis_card":
            if artifact.get("open_questions"):
                flags.append("리포트 주장 중 전망/가정 성격의 문장이 있어 추가 검증이 필요함")
            if not artifact.get("numbers"):
                flags.append("숫자 근거가 충분히 잡히지 않아 수치 검증이 필요함")
    return list(dict.fromkeys(flags))[:5]


def synthesize_artifacts(paths: list[Path]) -> InsightBrief:
    artifacts = [load_artifact(path) for path in paths]
    topics = top_items(
        (topic for artifact in artifacts for topic in collect_topics(artifact)),
        limit=6,
    )
    claims = top_items(
        (claim for artifact in artifacts for claim in collect_claims(artifact)),
        limit=6,
    )
    dates = top_items(
        (date for artifact in artifacts for date in collect_dates(artifact)),
        limit=2,
    )
    source_units = list(dict.fromkeys(ref for artifact in artifacts for ref in collect_source_units(artifact)))
    title = first_nonempty(
        [
            f"{dates[0]} insight brief" if dates else "",
            "knowledge pipeline insight brief",
        ]
    )
    return InsightBrief(
        title=title,
        core_mechanism=build_core_mechanism(topics, claims),
        connection_map=build_connection_map(topics, claims),
        investment_implications=build_investment_implications(topics),
        source_units=source_units,
        related_notes=build_related_notes(topics, source_units),
        confidence_flags=build_confidence_flags(artifacts),
        primary_topics=topics,
        effective_date=dates[0] if dates else None,
    )


def synthesize_to_brief(paths: list[Path], output_path: Path | None = None) -> Path:
    brief = synthesize_artifacts(paths)
    destination = output_path or Path("00. inbox") / "pipeline" / f"{brief.title.replace(' ', '_')}.insight_brief.json"
    write_json(destination, brief)
    return destination
