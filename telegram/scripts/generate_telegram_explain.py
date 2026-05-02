#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.report_pipeline import extract_thesis_card
from knowledge_pipeline.telegram_pipeline import parse_telegram_note


API_ROOT = "https://api.openai.com/v1"
DEFAULT_OUTPUT_ROOT = Path("/Users/ppingkku/Documents/stock/telegram")
DEFAULT_ENV_FILE = DEFAULT_OUTPUT_ROOT / ".env"
DEFAULT_PROMPT_FILE = DEFAULT_OUTPUT_ROOT / "prompts" / "telegram_explain_system_prompt.md"
DEFAULT_MODEL = "gpt-4.1-mini"
CURL_RETRY_COUNT = 2
CURL_RETRY_DELAY = 2
REQUEST_TIMEOUT = 180


@dataclass(frozen=True)
class ExplainContext:
    chat_name: str
    chat_slug: str
    date_key: str
    raw_note_path: Path
    explain_note_path: Path
    message_key: str
    timestamp: str
    sender: str
    forwarded_from: str
    topics: list[str]
    body_text: str
    attachments: list[Path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LLM-based supplementary explanations for Telegram raw notes."
    )
    parser.add_argument(
        "raw_note",
        type=Path,
        help="Telegram raw day note path under telegram/bot/...",
    )
    parser.add_argument(
        "--message-key",
        help="Only generate the explanation for one message_key inside the note.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Telegram output root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Path to the .env file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=DEFAULT_PROMPT_FILE,
        help=f"Prompt template file (default: {DEFAULT_PROMPT_FILE})",
    )
    parser.add_argument(
        "--model",
        default="",
        help=f"Override the OpenAI model. Defaults to OPENAI_MODEL or {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite an existing explanation block for the selected message(s).",
    )
    return parser.parse_args()


def load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def resolve_model(model_override: str = "") -> str:
    return model_override.strip() or os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL


def require_openai_api_key() -> str:
    token = os.environ.get("OPENAI_API_KEY", "").strip()
    if token:
        return token
    raise RuntimeError("OPENAI_API_KEY is missing. Add it to telegram/.env to enable explain generation.")


def format_curl_error(context: str, exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    details = [f"{context} failed (curl exit {exc.returncode})."]
    if stderr:
        details.append(stderr)
    return " ".join(details)


def call_openai_markdown(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        "max_output_tokens": 1800,
    }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False)
        payload_path = Path(handle.name)

    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-L",
                "--retry",
                str(CURL_RETRY_COUNT),
                "--retry-delay",
                str(CURL_RETRY_DELAY),
                "--retry-all-errors",
                "--connect-timeout",
                "15",
                "--max-time",
                str(REQUEST_TIMEOUT),
                f"{API_ROOT}/responses",
                "-H",
                f"Authorization: Bearer {api_key}",
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                f"@{payload_path}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(format_curl_error("OpenAI explain request", exc)) from exc
    finally:
        payload_path.unlink(missing_ok=True)

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI explain request returned invalid JSON.") from exc

    output_text = extract_response_text(response)
    if not output_text:
        raise RuntimeError("OpenAI explain request returned no text output.")
    return output_text.strip()


def extract_response_text(payload: Any) -> str:
    if isinstance(payload, dict):
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        output = payload.get("output")
        if isinstance(output, list):
            texts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []):
                    if not isinstance(content, dict):
                        continue
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
            if texts:
                return "\n\n".join(texts)

        for value in payload.values():
            extracted = extract_response_text(value)
            if extracted:
                return extracted

    if isinstance(payload, list):
        for item in payload:
            extracted = extract_response_text(item)
            if extracted:
                return extracted

    return ""


def note_path_to_chat_slug(raw_note_path: Path) -> str:
    parts = list(raw_note_path.resolve().parts)
    if "telegram" not in parts:
        raise RuntimeError(f"Unexpected raw note path: {raw_note_path}")
    telegram_index = parts.index("telegram")
    tail = parts[telegram_index + 1 :]
    if len(tail) < 5 or tail[0] != "bot":
        raise RuntimeError(f"Expected a telegram/bot note path, got: {raw_note_path}")
    return tail[1]


def note_path_to_chat_name(raw_note_path: Path) -> str:
    first_line = raw_note_path.read_text(encoding="utf-8").splitlines()[0].strip()
    if first_line.startswith("# ") and " | " in first_line:
        return first_line[2:].split(" | ", 1)[0].strip()
    return note_path_to_chat_slug(raw_note_path)


def explain_note_path_for_raw(raw_note_path: Path, output_root: Path) -> Path:
    slug = note_path_to_chat_slug(raw_note_path)
    date_key = raw_note_path.stem
    return output_root / "explain" / slug / date_key[:4] / date_key[5:7] / f"{date_key}.md"


def explain_note_header(chat_name: str, date_key: str) -> str:
    return "\n".join(
        [
            "---",
            f'title: "{chat_name} | {date_key} Explain"',
            'source: "Telegram Explain Layer"',
            f"date: {date_key}",
            "type: telegram_explain_note",
            "status: active_research",
            "topic: []",
            "tags:",
            "  - research",
            "  - telegram",
            "  - explain",
            "---",
            "",
            f"# {chat_name} | {date_key}",
            "",
            "> [!summary] TL;DR",
            "> Telegram raw note를 보완 설명하는 LLM 레이어입니다.",
            "",
            "> [!important] Rules",
            "> Fact와 interpretation을 분리하고, 기존 thesis와 연결, 더 나은 표현, anti-pattern 경고를 남깁니다.",
            "",
        ]
    )


def relpath_for_markdown(base_dir: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, start=base_dir)).as_posix().replace(" ", "%20")


def render_source_trail(context: ExplainContext, model: str) -> list[str]:
    lines = [
        "### Source Trail",
        f"- raw note: [{context.raw_note_path.name}]({relpath_for_markdown(context.explain_note_path.parent, context.raw_note_path)})",
        f"- llm_model: `{model}`",
    ]
    if context.forwarded_from:
        lines.append(f"- forwarded_from: `{context.forwarded_from}`")
    for attachment in context.attachments:
        label = attachment.name
        lines.append(f"- [{label}]({relpath_for_markdown(context.explain_note_path.parent, attachment)})")
    return lines


def detect_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def attachment_context_block(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            card = extract_thesis_card(path)
        except Exception as exc:
            return f"- PDF: {path.name}\n  - 추출 실패: {exc}"

        number_lines = [f"{fact.value} | {fact.context}" for fact in card.numbers[:6]]
        narrative_lines = card.narrative[:4]
        question_lines = card.open_questions[:3]
        lines = [
            f"- PDF: {path.name}",
            f"  - core_claim: {card.core_claim}",
        ]
        if narrative_lines:
            lines.append("  - narrative:")
            for item in narrative_lines:
                lines.append(f"    - {item}")
        if number_lines:
            lines.append("  - numbers:")
            for item in number_lines:
                lines.append(f"    - {item}")
        if question_lines:
            lines.append("  - open_questions:")
            for item in question_lines:
                lines.append(f"    - {item}")
        return "\n".join(lines)

    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return (
            f"- Image: {path.name}\n"
            "  - OCR/vision 분석은 현재 자동화에 포함되지 않음\n"
            "  - 텍스트/캡션과 파일명만 기반으로 보수적으로 설명할 것"
        )

    return f"- Attachment: {path.name}\n  - 자동 본문 추출 미지원. 파일명과 문맥만 참고할 것"


def build_user_prompt(context: ExplainContext) -> str:
    topic_text = ", ".join(context.topics) if context.topics else "없음"
    body_text = context.body_text.strip() or "(본문 텍스트 없음)"
    attachment_blocks = [attachment_context_block(path) for path in context.attachments]
    attachment_text = "\n".join(attachment_blocks) if attachment_blocks else "- 없음"

    return "\n".join(
        [
            "아래 Telegram 메시지/자료에 대해 보충설명을 작성하라.",
            "",
            "메시지 메타데이터:",
            f"- chat_name: {context.chat_name}",
            f"- chat_slug: {context.chat_slug}",
            f"- date: {context.date_key}",
            f"- timestamp: {context.timestamp}",
            f"- message_key: {context.message_key}",
            f"- sender: {context.sender}",
            f"- forwarded_from: {context.forwarded_from or '없음'}",
            f"- topic_candidates: {topic_text}",
            "",
            "본문:",
            body_text,
            "",
            "첨부 해석 컨텍스트:",
            attachment_text,
            "",
            "출력은 반드시 한국어 markdown으로 아래 섹션만 포함하라.",
            "섹션 제목과 순서를 유지하라.",
            "",
            "### Facts That Matter",
            "- 사실 3~6개",
            "",
            "### 보충설명",
            "- 원문이 왜 중요한지, 어떤 구조를 말하는지, 숫자/메커니즘 관점에서 짧은 문단 1~3개",
            "",
            "### 기존 thesis와 연결",
            "- 강화: ...",
            "- 약화: ...",
            "- 무관/관찰: ...",
            "",
            "### Better Expression",
            "- 더 좋은 표현 종목/ETF/대장주 관점 또는 왜 아직 보류해야 하는지",
            "",
            "### Do Not Misread",
            "- borrowed thesis, 추격, expression error, mixed stop logic 관점 경고 2~4개",
            "",
            "### Next Checkpoints",
            "- 3~6개월 내 확인할 숫자/공시/이벤트 2~5개",
            "",
            "### Invalidation",
            "- 이 해석이 약해지거나 틀렸다고 볼 조건 1~3개",
            "",
            "규칙:",
            "- buy/sell/비중 같은 직접 실행 지시는 하지 마라.",
            "- 사실과 해석을 섞지 마라.",
            "- 숫자, 시간축, 무효화 조건이 없는 낙관론을 쓰지 마라.",
            "- 본문/첨부에서 확인되지 않은 내용은 단정하지 말고 '추가 확인 필요'로 써라.",
            "- 이미지 첨부는 자동 OCR/vision 미지원이므로 과잉 해석하지 마라.",
        ]
    )


def append_block_if_missing(
    *,
    note_path: Path,
    header: str,
    message_key: str,
    block: str,
    force: bool,
) -> bool:
    existing = note_path.read_text(encoding="utf-8") if note_path.exists() else header
    if not note_path.exists():
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(header, encoding="utf-8")
        existing = header

    marker = f"`{message_key}`"
    if marker in existing and not force:
        return False

    if marker in existing and force:
        lines = existing.splitlines()
        rebuilt: list[str] = []
        skip = False
        for line in lines:
            if line.startswith("## ") and marker in "\n".join(rebuilt[-3:]):
                skip = False
            if line.startswith("## ") and skip:
                skip = False
            if not skip:
                rebuilt.append(line)
            if line == f"- message_key: `{message_key}`":
                rebuilt = rebuilt[:-2]
                skip = True
        existing = "\n".join(rebuilt).rstrip() + "\n\n"

    separator = "" if existing.endswith("\n\n") else "\n"
    note_path.write_text(f"{existing}{separator}{block}", encoding="utf-8")
    return True


def render_explain_block(*, context: ExplainContext, markdown_body: str, model: str) -> str:
    lines = [
        f"## {context.timestamp} | {context.sender}",
        f"- message_key: `{context.message_key}`",
    ]
    if context.topics:
        lines.append(f"- topic: {', '.join(context.topics)}")
    lines.append("")
    lines.append(markdown_body.strip())
    lines.append("")
    lines.extend(render_source_trail(context, model))
    lines.append("")
    return "\n".join(lines)


def load_system_prompt(prompt_path: Path) -> str:
    if not prompt_path.exists():
        raise RuntimeError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def explain_context_from_raw_note(
    *,
    raw_note_path: Path,
    output_root: Path,
    message_key_filter: str | None = None,
) -> list[ExplainContext]:
    raw_note_path = raw_note_path.expanduser().resolve()
    messages = parse_telegram_note(raw_note_path)
    chat_name = note_path_to_chat_name(raw_note_path)
    chat_slug = note_path_to_chat_slug(raw_note_path)
    explain_note_path = explain_note_path_for_raw(raw_note_path, output_root)

    contexts: list[ExplainContext] = []
    for message in messages:
        if message_key_filter and message.message_key != message_key_filter:
            continue
        contexts.append(
            ExplainContext(
                chat_name=chat_name,
                chat_slug=chat_slug,
                date_key=raw_note_path.stem,
                raw_note_path=raw_note_path,
                explain_note_path=explain_note_path,
                message_key=message.message_key,
                timestamp=message.timestamp,
                sender=message.sender,
                forwarded_from=message.forwarded_from,
                topics=message.topics,
                body_text=message.body_text,
                attachments=[Path(item).resolve() for item in message.attachments],
            )
        )
    return contexts


def generate_explain_for_context(
    *,
    context: ExplainContext,
    api_key: str,
    model: str,
    system_prompt: str,
    force: bool = False,
) -> bool:
    markdown_body = call_openai_markdown(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        user_prompt=build_user_prompt(context),
    )
    block = render_explain_block(context=context, markdown_body=markdown_body, model=model)
    return append_block_if_missing(
        note_path=context.explain_note_path,
        header=explain_note_header(context.chat_name, context.date_key),
        message_key=context.message_key,
        block=block,
        force=force,
    )


def maybe_store_message_explain(
    *,
    output_root: Path,
    env_file: Path,
    prompt_file: Path,
    chat_name: str,
    chat_slug: str,
    date_key: str,
    raw_note_path: Path,
    message_key: str,
    timestamp: str,
    sender: str,
    forwarded_from: str,
    topics: list[str],
    body_text: str,
    attachments: list[tuple[Path, str]],
    model_override: str = "",
) -> bool:
    load_dotenv(env_file)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return False

    context = ExplainContext(
        chat_name=chat_name,
        chat_slug=chat_slug,
        date_key=date_key,
        raw_note_path=raw_note_path.resolve(),
        explain_note_path=output_root / "explain" / chat_slug / date_key[:4] / date_key[5:7] / f"{date_key}.md",
        message_key=message_key,
        timestamp=timestamp,
        sender=sender,
        forwarded_from=forwarded_from,
        topics=topics,
        body_text=body_text,
        attachments=[path.resolve() for path, _ in attachments],
    )
    model = resolve_model(model_override)
    system_prompt = load_system_prompt(prompt_file)
    try:
        return generate_explain_for_context(
            context=context,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        print(f"[telegram-explain] {message_key}: {exc}", file=sys.stderr)
        return False


def main() -> None:
    args = parse_args()
    try:
        load_dotenv(args.env_file.expanduser().resolve())
        api_key = require_openai_api_key()
        model = resolve_model(args.model)
        prompt_file = args.prompt_file.expanduser().resolve()
        output_root = args.output_root.expanduser().resolve()
        raw_note_path = args.raw_note.expanduser().resolve()
        contexts = explain_context_from_raw_note(
            raw_note_path=raw_note_path,
            output_root=output_root,
            message_key_filter=args.message_key,
        )
        if not contexts:
            raise RuntimeError(f"No message found for {raw_note_path}")

        system_prompt = load_system_prompt(prompt_file)
        generated = 0
        for context in contexts:
            written = generate_explain_for_context(
                context=context,
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                force=args.force,
            )
            if written:
                generated += 1

        print(
            f"Generated {generated} explain block(s). Explain note: {contexts[0].explain_note_path}",
            file=sys.stdout,
        )
    except RuntimeError as exc:
        print(f"[telegram-explain] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
