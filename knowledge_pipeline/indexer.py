from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


INDEX_TARGETS = [
    ("기업", "기업"),
    ("산업", "산업"),
    ("매크로", "매크로"),
    ("인사이트 & 뉴스", "인사이트 & 뉴스"),
    ("10. Daily note", "Daily Notes"),
    ("WSJ", "WSJ"),
]

NOTE_TYPE_BY_ROOT = {
    "기업": "company",
    "산업": "industry",
    "매크로": "macro",
    "인사이트 & 뉴스": "insight",
    "10. Daily note": "daily_note",
    "WSJ": "wsj",
}

DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
INSIGHT_SUMMARY_OVERRIDES = {
    "2026-02-17_인사이트.md": "비중국 태양광·금융 주주환원·소부장 로테이션 점검",
    "2026-02-18_인사이트.md": "메타 DC 증설로 칩 병목이 이어지고 SaaS 멀티플은 흔들림",
    "2026-02-19_인사이트.md": "코스닥 부양 기대와 소부장·태양광·공작기계 로테이션",
    "2026-02-20_인사이트.md": "보험 주주환원 기대와 방산·차부품 순환매 점검",
    "2026-03-04 인사이트.md": "호르무즈 리스크가 인플레·환율·조정장을 흔드는지 점검",
    "2026-03-05 인사이트.md": "상법 개정이 금융·지배구조 리레이팅을 밀어주는 국면",
    "2026-03-06 인사이트.md": "클래리티법이 크립토를 월가·국채 수요 체계로 편입",
    "2026-03-07 인사이트.md": "중동 리스크가 유가·달러·실질금리를 통해 성장주를 압박",
    "2026-03-09 인사이트.md": "유가 쇼크 속 한국은 에너지·환율·외국인 수급에 가장 민감",
    "2026-03-11 인사이트.md": "AT&T-위성 통합 투자로 게이트웨이 안테나 수요 확대",
    "2026-03-14~15 인사이트.md": "전쟁 뉴스보다 에너지 안보·전력 인프라 병목이 핵심",
    "2026-03-16 인사이트.md": "원전·CPO·OTA처럼 병목을 푸는 플레이어가 수혜",
    "2026-03-18 인사이트.md": "AI CAPEX가 패키징·전력으로 번지며 OCI 논리와 연결",
    "2026-03-19 인사이트.md": "숏 포지셔닝과 실물 공급 병목의 괴리가 커지는 국면",
    "2026-03-20 인사이트.md": "에너지 충격이 금리·AI economics·대만 리스크를 동시 재가격화",
    "2026-03-21 인사이트.md": "3차 벤처붐도 코스닥 전체보다 Exit·병목·상업화가 핵심",
    "2026-03-22 인사이트.md": "AI 강세보다 수출통제·세후이익·에너지 비용 재분배가 핵심",
    "2026-03-25 인사이트.md": "AI 수요가 반도체에서 전력·냉각·정책 병목으로 이동",
    "2026-03-28 인사이트.md": "AI와 지정학이 자본을 다시 전력·패키징·유형자산으로 이동",
    "2026-04-03 인사이트.md": "원유보다 디젤·LNG 물류 같은 배달 가능한 에너지가 핵심",
    "2026-04-04 인사이트.md": "에너지 급등 국면에선 규제에 덜 뺏기는 인프라가 중요",
    "2026-04-05 인사이트.md": "효율 개선에도 usable power·BESS·액랭 병목은 더 깊어진다",
}
DAILY_NOTE_SUMMARY_OVERRIDES = {
    "2026-02-17.md": "반도체 집중 대신 태양광·공작기계·삼양식품 대안 탐색",
    "2026-03-03.md": "호르무즈 리스크가 유가·환율·외국인 수급을 동시 압박",
    "2026-03-04.md": "전쟁발 유가 충격은 한국에서 환율·수급으로 증폭",
    "2026-03-09.md": "유가 100달러와 헬륨 급등이 코스닥 로테이션 판단 변수",
    "2026-03-10.md": "자사주 소각과 에너지 안보가 지주사·LNG·태양광을 자극",
    "2026-03-14.md": "전쟁 장기화 우려 속 시장은 방향성 결정을 앞둔 구간",
}
WSJ_SUMMARY_OVERRIDES = {
    "2026-03-19 WSJ 인사이트.md": "AI 스택 수출·전력 병목·전쟁 인플레가 한 체인으로 연결",
    "AI수출외교와한국데이터센터_260319.md": "미국 AI 패키지 수출의 병목은 한국 데이터센터 전력",
    "엔비디아_추론과중국재진입_260319.md": "엔비디아 핵심은 추론 economics와 중국 규제형 매출",
    "중국소비전환과전쟁인플레_260319.md": "중국 회복은 소비 전환과 전쟁발 인플레의 충돌이 핵심",
    "호르무즈_에너지안보와전력체인_260319.md": "호르무즈 리스크는 유가보다 전력·에너지 안보 체인을 자극",
    "2026-03-20 WSJ 인사이트.md": "유가 충격이 금리·AI 비용·대만 전력 리스크로 연쇄 전이",
    "대만_전력민감도와안보리스크_260320.md": "대만 AI 프리미엄은 반도체보다 전력·LNG·안보까지 봐야 함",
    "엔비디아_AI토큰경제_260320.md": "AI 투자 논리는 GPU보다 토큰 비용과 추론 economics",
    "연준_정치화와정책공백_260320.md": "연준 변수는 금리보다 정치화와 정책 공백 리스크",
    "유가쇼크와금리경로_260320.md": "유가 쇼크의 1차 충격은 침체보다 할인율과 금리 경로",
    "크리스토퍼심스_VAR와충격해석_260320.md": "VAR 관점에선 유가 충격의 전이 경로가 투자 핵심",
    "2026-03-22 WSJ 인사이트.md": "AI 통제·에너지 쇼크·교역 둔화가 할인율 상단을 밀어올림",
    "IEA_수요파괴와에너지비상대응_260322.md": "에너지 충격이 가격 문제를 넘어 비상 수요관리 단계로 이동",
    "글로벌교역둔화와AI완충_260322.md": "전쟁이 교역을 누르지만 AI 상품이 일부 완충재로 작동",
    "대중국_우회수출과AI통제_260322.md": "중국 AI 수요는 우회수출·서버 컴플라이언스 리스크로 이동",
    "법인세공시와클린에너지세액공제_260322.md": "세후 이익과 클린에너지 세액공제가 새 profit pool 변수",
    "에너지쇼크와침체임계_260322.md": "침체보다 higher for longer 금리와 멀티플 압축이 우선",
}


def normalize_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


@dataclass
class NoteIndexEntry:
    path: Path
    wiki_link: str
    title: str
    note_type: str
    updated_at: str
    sector_tags: list[str]
    core_summary: str


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    metadata: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, text[match.end() :]


def wiki_link_for(path: Path, vault_root: Path) -> str:
    relative = path.relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def normalize_inline_text(value: str) -> str:
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"!\[\[([^\]]+)\]\]", "", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = value.replace("==", "")
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_]+)__", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_sector_tags(text: str, title: str, note_type: str) -> list[str]:
    if note_type != "company":
        return []

    metadata, body = split_frontmatter(text)
    candidates: list[str] = []

    metadata_tags = metadata.get("tags", "")
    if metadata_tags:
        for token in metadata_tags.split(","):
            token = token.strip().lstrip("#")
            if token:
                candidates.append(token)

    for line in body.splitlines()[:12]:
        for match in re.findall(r"(?<!\w)#([0-9A-Za-z가-힣_/\-]+)", line):
            candidates.append(match.strip())

    normalized_title = normalize_name(title)
    tags: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        if normalize_name(candidate) == normalized_title:
            continue
        if candidate.isdigit():
            continue
        key = normalize_name(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        tags.append(f"#{candidate}")
        if len(tags) >= 6:
            break
    return tags


def extract_updated_at(path: Path, text: str) -> str:
    if DATE_RE.fullmatch(path.stem):
        return path.stem

    metadata, body = split_frontmatter(text)
    for key in ("updated_at", "date"):
        value = metadata.get(key, "")
        if DATE_RE.fullmatch(value):
            return value

    dates = DATE_RE.findall(body)
    if dates:
        return max(dates)

    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def shorten_summary(value: str, limit: int = 54) -> str:
    value = normalize_inline_text(value).strip(" -:;,.")
    if len(value) <= limit:
        return value
    truncated = value[:limit].rstrip(" -:;,.")
    return f"{truncated}…"


def extract_insight_callout(body: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if "Industry Mechanism" not in line:
            continue

        collected: list[str] = []
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if not stripped:
                if collected:
                    break
                continue
            if not stripped.startswith(">"):
                if collected:
                    break
                continue

            text = stripped.lstrip(">").strip()
            if not text or text.startswith("[!") or "Market Pitfall" in text:
                if collected:
                    break
                continue
            collected.append(text)

        if collected:
            return " ".join(collected)

    return ""


def extract_first_content_block(body: str) -> str:
    collected: list[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == "---":
            if collected:
                break
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("> [!"):
            continue

        cleaned = re.sub(r"^[-*+]\s*", "", stripped)
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
        cleaned = normalize_inline_text(cleaned)
        if not cleaned:
            continue
        collected.append(cleaned)
        if len(collected) >= 3:
            break
    return " ".join(collected)


def extract_insight_summary(path: Path, text: str, note_type: str) -> str:
    if note_type != "insight":
        return ""

    override = INSIGHT_SUMMARY_OVERRIDES.get(path.name)
    if override:
        return shorten_summary(override)

    _, body = split_frontmatter(text)
    candidate = extract_insight_callout(body) or extract_first_content_block(body)
    if not candidate:
        return ""

    candidate = normalize_inline_text(candidate)
    candidate = re.sub(r"^(?:오늘|이번)(?:의)?\s*핵심은\s*", "", candidate)
    candidate = re.sub(r"\s*즉\s+.*$", "", candidate)
    candidate = re.sub(r"\s*따라서\s+.*$", "", candidate)
    candidate = candidate.rstrip(".")
    candidate = re.sub(r"다$", "", candidate)
    return shorten_summary(candidate)


def extract_daily_note_summary(path: Path, text: str, note_type: str) -> str:
    if note_type != "daily_note":
        return ""

    override = DAILY_NOTE_SUMMARY_OVERRIDES.get(path.name)
    if override:
        return shorten_summary(override)

    _, body = split_frontmatter(text)
    match = re.search(r"\*\*전체 한 줄\*\*\s*:\s*(.+)", body)
    if match:
        return shorten_summary(match.group(1))

    after_review = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("# 오늘 회고"):
            after_review = True
            continue
        if not after_review:
            continue
        if not stripped:
            continue

        cleaned = re.sub(r"^[-*+]\s*", "", stripped)
        cleaned = normalize_inline_text(cleaned)
        if not cleaned:
            continue
        return shorten_summary(cleaned)

    return ""


def extract_wsj_summary(path: Path, text: str, note_type: str) -> str:
    if note_type != "wsj":
        return ""

    override = WSJ_SUMMARY_OVERRIDES.get(path.name)
    if override:
        return shorten_summary(override)

    _, body = split_frontmatter(text)
    candidate = extract_insight_callout(body)
    if candidate:
        candidate = normalize_inline_text(candidate)
        candidate = re.sub(r"^(?:오늘|이번)(?:의)?\s*(?:WSJ\s*)?(?:스크랩|기사|뉴스)?(?:의)?\s*핵심(?:은)?\s*", "", candidate)
        candidate = re.sub(r"\s*즉\s+.*$", "", candidate)
        candidate = candidate.rstrip(".")
        return shorten_summary(candidate)

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            return shorten_summary(stripped[2:])

    return ""


def extract_core_summary(path: Path, text: str, note_type: str) -> str:
    return (
        extract_insight_summary(path, text, note_type)
        or extract_daily_note_summary(path, text, note_type)
        or extract_wsj_summary(path, text, note_type)
    )


def note_type_for(path: Path, vault_root: Path) -> str:
    relative = path.relative_to(vault_root)
    top_level = relative.parts[0] if relative.parts else ""
    return NOTE_TYPE_BY_ROOT.get(top_level, "note")


def collect_index_entries(vault_root: Path, folder_name: str) -> list[NoteIndexEntry]:
    root = vault_root / folder_name
    if not root.exists():
        return []

    entries: list[NoteIndexEntry] = []
    for note_path in root.rglob("*.md"):
        if note_path.name == "index.md":
            continue
        text = note_path.read_text(encoding="utf-8")
        note_type = note_type_for(note_path, vault_root)
        entries.append(
            NoteIndexEntry(
                path=note_path,
                wiki_link=wiki_link_for(note_path, vault_root),
                title=note_path.stem,
                note_type=note_type,
                updated_at=extract_updated_at(note_path, text),
                sector_tags=extract_sector_tags(text, note_path.stem, note_type),
                core_summary=extract_core_summary(note_path, text, note_type),
            )
        )

    return sorted(entries, key=lambda entry: (entry.updated_at, entry.title), reverse=True)


def render_index_section(section_title: str, entries: list[NoteIndexEntry]) -> list[str]:
    lines = [f"## {section_title}", ""]
    if not entries:
        lines.extend(["- 없음", ""])
        return lines

    if section_title == "기업":
        lines.extend(
            [
                "| 노트 | 최근 업데이트 | 섹터 |",
                "| --- | --- | --- |",
            ]
        )
        for entry in entries:
            sectors = " ".join(entry.sector_tags) if entry.sector_tags else "-"
            lines.append(f"| {entry.wiki_link} | {entry.updated_at} | {sectors} |")
    elif section_title == "인사이트 & 뉴스":
        lines.extend(
            [
                "| 노트 | 최근 업데이트 | 핵심 |",
                "| --- | --- | --- |",
            ]
        )
        for entry in entries:
            core_summary = entry.core_summary or "-"
            lines.append(f"| {entry.wiki_link} | {entry.updated_at} | {core_summary} |")
    elif section_title in {"Daily Notes", "WSJ"}:
        lines.extend(
            [
                "| 노트 | 최근 업데이트 | 핵심 |",
                "| --- | --- | --- |",
            ]
        )
        for entry in entries:
            core_summary = entry.core_summary or "-"
            lines.append(f"| {entry.wiki_link} | {entry.updated_at} | {core_summary} |")
    else:
        lines.extend(
            [
                "| 노트 | 최근 업데이트 |",
                "| --- | --- |",
            ]
        )
        for entry in entries:
            lines.append(f"| {entry.wiki_link} | {entry.updated_at} |")
    lines.append("")
    return lines


def build_index_text(vault_root: Path) -> str:
    sections: list[tuple[str, list[NoteIndexEntry]]] = [
        (section_title, collect_index_entries(vault_root, folder_name))
        for folder_name, section_title in INDEX_TARGETS
    ]

    total_notes = sum(len(entries) for _, entries in sections)
    lines = [
        "# Stock Wiki Index",
        "",
        "> [!info] Auto-generated Index",
        "> This file is regenerated by `python3 -m knowledge_pipeline reindex`.",
        "",
        "## Snapshot",
        "",
        "| 구분 | 개수 |",
        "| --- | ---: |",
        f"| total_notes | {total_notes} |",
    ]

    for section_title, entries in sections:
        lines.append(f"| {section_title} | {len(entries)} |")
    lines.append("")

    for section_title, entries in sections:
        lines.extend(render_index_section(section_title, entries))

    return "\n".join(lines).rstrip() + "\n"


def ensure_log_file(vault_root: Path) -> Path:
    log_path = vault_root / "log.md"
    if log_path.exists():
        return log_path

    log_path.write_text(
        "# Wiki Log\n\n"
        "> [!info] Append-only Log\n"
        "> Ingest, query write-back, lint, and major wiki maintenance events should be recorded here.\n",
        encoding="utf-8",
    )
    return log_path


def write_index(vault_root: Path) -> Path:
    index_path = vault_root / "index.md"
    index_path.write_text(build_index_text(vault_root), encoding="utf-8")
    return index_path


def reindex_vault(vault_root: Path) -> tuple[Path, Path]:
    root = vault_root.resolve()
    index_path = write_index(root)
    log_path = ensure_log_file(root)
    return index_path, log_path
