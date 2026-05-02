from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from .schemas import NumberFact, ThesisCard, VerificationUnit
from .utils import cleaned_lines, extract_numbers, infer_filename_candidates, sha256_text, write_json


CLAIM_HINTS = ("핵심", "포인트", "주목", "투자", "수혜", "개선", "확대", "성장", "증가", "회복", "병목")
SOFT_WORDS = ("예상", "전망", "추정", "가능성", "가정", "기대", "우려", "판단")


def thesis_card_path_for_pdf(pdf_path: Path) -> Path:
    return pdf_path.resolve().parent / "artifacts" / f"{pdf_path.stem}.thesis_card.json"


def extract_page_texts(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    texts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        texts.append(text)
    return texts


def clean_title(pdf_path: Path, page_texts: list[str]) -> str:
    if page_texts:
        first_page = cleaned_lines(page_texts[0])
        if first_page:
            title = first_page[0]
            if len(title) >= 6:
                return title[:160]
    return pdf_path.stem.replace("_", " ").strip()


def sentence_candidates(text: str) -> list[str]:
    normalized = text.replace("•", "\n").replace("·", "\n")
    parts = re.split(r"[\n。]|(?<=[.!?])\s+", normalized)
    return [part.strip() for part in parts if part and len(part.strip()) >= 12]


def derive_core_claim(page_texts: list[str], pdf_path: Path) -> str:
    for page_text in page_texts[:3]:
        for sentence in sentence_candidates(page_text):
            if any(hint in sentence for hint in CLAIM_HINTS):
                return sentence[:240]
        lines = cleaned_lines(page_text)
        if len(lines) >= 2:
            return f"{lines[0]} {lines[1]}"[:240]
    return f"{pdf_path.stem}의 핵심 주장 추출이 제한되어 추가 해석이 필요합니다."


def derive_verification_units(page_texts: list[str]) -> list[VerificationUnit]:
    units: list[VerificationUnit] = []
    seen: set[str] = set()
    for page_index, page_text in enumerate(page_texts[:6], start=1):
        lines = cleaned_lines(page_text)
        for idx, line in enumerate(lines):
            if len(line) < 12:
                continue
            if not any(hint in line for hint in CLAIM_HINTS) and idx > 10:
                continue
            signature = re.sub(r"\d[\d,./%-]*", "", line)
            signature = re.sub(r"\s+", " ", signature).strip().lower()
            if not signature or signature in seen:
                continue
            seen.add(signature)
            evidence = [line]
            for next_line in lines[idx + 1 : idx + 3]:
                if len(next_line) >= 8:
                    evidence.append(next_line)
            weakness = ""
            if any(token in line for token in SOFT_WORDS):
                weakness = "가정/전망 표현이 포함돼 후속 검증이 필요함"
            units.append(
                VerificationUnit(
                    label=f"VU{len(units) + 1}",
                    claim=line[:200],
                    evidence=evidence,
                    page_refs=[page_index],
                    weakness=weakness,
                )
            )
            if len(units) >= 8:
                return units
    return units


def derive_narrative(page_texts: list[str]) -> list[str]:
    narrative: list[str] = []
    seen: set[str] = set()
    for page_text in page_texts[:5]:
        for sentence in sentence_candidates(page_text):
            digit_ratio = sum(char.isdigit() for char in sentence) / max(len(sentence), 1)
            if digit_ratio > 0.25:
                continue
            if sentence in seen:
                continue
            seen.add(sentence)
            narrative.append(sentence[:240])
            if len(narrative) >= 6:
                return narrative
    return narrative


def derive_number_facts(page_texts: list[str]) -> list[NumberFact]:
    facts: list[NumberFact] = []
    seen: set[tuple[str, str]] = set()
    for page_index, page_text in enumerate(page_texts, start=1):
        lines = cleaned_lines(page_text)
        for line in lines:
            values = extract_numbers(line, limit=6)
            for value in values:
                key = (value, line[:80])
                if key in seen:
                    continue
                seen.add(key)
                facts.append(NumberFact(value=value, context=line[:220], page_ref=page_index))
                if len(facts) >= 25:
                    return facts
    return facts


def derive_open_questions(
    *,
    core_claim: str,
    verification_units: list[VerificationUnit],
    narrative: list[str],
    numbers: list[NumberFact],
) -> list[str]:
    questions: list[str] = []
    if not numbers:
        questions.append("숫자 근거가 빈약해 추가 표/그래프 확인이 필요함")
    if not verification_units:
        questions.append("주장 구조 분해가 충분치 않아 사람이 최소 검증 단위를 다시 잡아야 함")
    if any(token in core_claim for token in SOFT_WORDS):
        questions.append("핵심 주장에 전망/가정 표현이 많아 실제 트리거와 확인 지표를 별도 설정해야 함")
    if narrative and not any("병목" in item or "수익" in item for item in narrative):
        questions.append("산업 메커니즘보다 현상 설명 비중이 높아 profit pool 설명 보강이 필요함")
    return questions[:4]


def extract_thesis_card(pdf_path: Path) -> ThesisCard:
    page_texts = extract_page_texts(pdf_path)
    title = clean_title(pdf_path, page_texts)
    core_claim = derive_core_claim(page_texts, pdf_path)
    verification_units = derive_verification_units(page_texts)
    narrative = derive_narrative(page_texts)
    numbers = derive_number_facts(page_texts)
    page_refs = sorted({fact.page_ref for fact in numbers if fact.page_ref is not None})
    if not page_refs and page_texts:
        page_refs = list(range(1, min(len(page_texts), 3) + 1))
    return ThesisCard(
        doc_id=sha256_text(str(pdf_path.resolve()), title),
        title=title,
        source_path=str(pdf_path.resolve()),
        core_claim=core_claim,
        verification_units=verification_units,
        narrative=narrative,
        numbers=numbers,
        page_refs=page_refs,
        ticker_theme_candidates=infer_filename_candidates(pdf_path.stem),
        open_questions=derive_open_questions(
            core_claim=core_claim,
            verification_units=verification_units,
            narrative=narrative,
            numbers=numbers,
        ),
    )


def extract_pdf_to_card(pdf_path: Path, output_path: Path | None = None) -> Path:
    card = extract_thesis_card(pdf_path)
    destination = output_path or thesis_card_path_for_pdf(pdf_path)
    write_json(destination, card)
    return destination
