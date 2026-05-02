#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.telegram_pipeline import manifest_path_for_note
from knowledge_pipeline.schemas import RawManifestRecord
from knowledge_pipeline.utils import append_jsonl_record, sha256_text


API_ROOT = "https://api.telegram.org"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "telegram"
DEFAULT_TIMEZONE = "Asia/Seoul"
DEFAULT_STATE_FILE = "telegram_bot_state.json"
DEFAULT_POLL_TIMEOUT = 25
DEFAULT_ENV_FILE = DEFAULT_OUTPUT_ROOT / ".env"
CURL_RETRY_COUNT = 3
CURL_RETRY_DELAY = 2
DOWNLOAD_TIMEOUT = 120
BOT_API_RETENTION_HOURS = 24
STALE_POLL_WARNING_HOURS = 20
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
CURL_ERROR_HINTS = {
    6: "Could not resolve the Telegram host. Check DNS or sandbox network restrictions.",
    7: "Failed to connect to the Telegram host. Check outbound network access.",
    28: "The Telegram request timed out. Check network latency or increase the timeout.",
}


@dataclass(frozen=True)
class FileSpec:
    kind: str
    file_id: str
    filename_hint: str
    mime_type: str


@dataclass(frozen=True)
class MessageAnalysis:
    topics: list[str]
    content_type: str
    one_liner: str
    key_points: list[str]
    interpretations: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll a Telegram bot inbox and store messages as daily markdown notes."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Destination root for notes and state (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone used for daily grouping (default: {DEFAULT_TIMEZONE})",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=DEFAULT_POLL_TIMEOUT,
        help=f"Long polling timeout in seconds (default: {DEFAULT_POLL_TIMEOUT})",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Path to the offset state file. Defaults to <output-root>/state/telegram_bot_state.json",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Path to the .env file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep polling for new messages until interrupted",
    )
    return parser.parse_args()


def require_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")


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


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    cleaned = re.sub(r"[-\s]+", "-", cleaned)
    return cleaned or "telegram-chat"


def sanitize_filename(name: str) -> str:
    if not name.strip():
        return "file"
    sanitized = re.sub(r"[^0-9A-Za-z._-]+", "_", name)
    sanitized = sanitized.strip("._")
    return sanitized or "file"


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"offset": 0}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def bootstrap_last_message_ids(output_root: Path) -> dict[str, int]:
    bot_root = output_root / "bot"
    if not bot_root.exists():
        return {}

    last_ids: dict[str, int] = {}
    pattern = re.compile(r"message_key: `(?P<chat>-?\d+):(?P<message>\d+)`")

    for note_path in sorted(bot_root.rglob("*.md")):
        text = note_path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            chat_id = match.group("chat")
            message_id = int(match.group("message"))
            last_ids[chat_id] = max(last_ids.get(chat_id, 0), message_id)

    return last_ids


def raw_updates_path(output_root: Path, captured_at: datetime) -> Path:
    date_key = captured_at.date().isoformat()
    return (
        output_root
        / "raw_updates"
        / "telegram_bot"
        / date_key[:4]
        / date_key[5:7]
        / f"{date_key}.raw_updates.jsonl"
    )


def gap_audit_path(output_root: Path) -> Path:
    return output_root / "state" / "telegram_bot_gap_audit.jsonl"


def append_gap_audit(output_root: Path, record: dict[str, Any]) -> None:
    destination = gap_audit_path(output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")


def append_raw_updates(
    *,
    output_root: Path,
    timezone: ZoneInfo,
    fetch_offset: int,
    updates: list[dict[str, Any]],
) -> None:
    if not updates:
        return

    captured_at = datetime.now(tz=timezone)
    destination = raw_updates_path(output_root, captured_at)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("a", encoding="utf-8") as handle:
        for update in updates:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else {}
            sender = message.get("from") if isinstance(message, dict) else {}
            message_date = message.get("date") if isinstance(message, dict) else None
            message_date_local = None
            if isinstance(message_date, int):
                message_date_local = datetime.fromtimestamp(
                    message_date,
                    tz=timezone,
                ).isoformat()

            record = {
                "captured_at": captured_at.isoformat(),
                "fetch_offset": fetch_offset,
                "update_id": update.get("update_id") if isinstance(update, dict) else None,
                "top_level_keys": sorted(update.keys()) if isinstance(update, dict) else [],
                "chat_id": chat.get("id") if isinstance(chat, dict) else None,
                "chat_type": chat.get("type") if isinstance(chat, dict) else None,
                "message_id": message.get("message_id") if isinstance(message, dict) else None,
                "message_date_local": message_date_local,
                "sender_id": sender.get("id") if isinstance(sender, dict) else None,
                "payload": update,
            }
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def format_curl_error(context: str, exc: subprocess.CalledProcessError) -> str:
    details = [f"{context} failed (curl exit {exc.returncode})."]
    stderr = (exc.stderr or "").strip()
    if stderr:
        details.append(stderr)
    hint = CURL_ERROR_HINTS.get(exc.returncode)
    if hint:
        details.append(hint)
    return " ".join(details)


def api_get_json(token: str, method: str, params: dict[str, Any], timeout: int) -> Any:
    filtered = {key: value for key, value in params.items() if value is not None}
    encoded = urlencode(filtered, doseq=True)
    url = f"{API_ROOT}/bot{token}/{method}"
    if encoded:
        url = f"{url}?{encoded}"

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
                str(timeout + 15),
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(format_curl_error(f"Telegram API call {method}", exc)) from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Telegram API call {method} returned invalid JSON.") from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error calling {method}: {payload}")
    return payload["result"]


def get_updates(token: str, offset: int, timeout: int) -> list[dict[str, Any]]:
    return api_get_json(
        token,
        "getUpdates",
        {
            "offset": offset,
            "limit": 100,
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        },
        timeout=timeout,
    )


def resolve_file_path(token: str, file_id: str) -> str:
    result = api_get_json(token, "getFile", {"file_id": file_id}, timeout=60)
    file_path = result.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        raise RuntimeError(f"Telegram getFile returned no path for file_id={file_id}")
    return file_path


def download_file(token: str, file_path: str, destination: Path) -> None:
    encoded_path = quote(file_path, safe="/")
    url = f"{API_ROOT}/file/bot{token}/{encoded_path}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_name(f".{destination.name}.part")
    temp_destination.unlink(missing_ok=True)

    try:
        subprocess.run(
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
                str(DOWNLOAD_TIMEOUT),
                "-o",
                str(temp_destination),
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        temp_destination.unlink(missing_ok=True)
        raise RuntimeError(format_curl_error(f"Telegram file download {file_path}", exc)) from exc

    temp_destination.replace(destination)


def chat_display_name(chat: dict[str, Any]) -> str:
    title = chat.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    first_name = str(chat.get("first_name") or "").strip()
    last_name = str(chat.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part)
    if full_name:
        return full_name

    username = chat.get("username")
    if isinstance(username, str) and username.strip():
        return f"@{username.strip()}"

    return f"chat-{chat.get('id', 'unknown')}"


def chat_slug(chat: dict[str, Any]) -> str:
    name = chat_display_name(chat)
    chat_id = str(chat.get("id", "unknown"))
    normalized_id = re.sub(r"[^0-9A-Za-z_-]", "-", chat_id).strip("-") or "unknown"
    return f"{slugify(name)}-{normalized_id}"


def sender_name(message: dict[str, Any]) -> str:
    sender = message.get("from") or {}
    first_name = str(sender.get("first_name") or "").strip()
    last_name = str(sender.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part)
    if full_name:
        return full_name

    username = sender.get("username")
    if isinstance(username, str) and username.strip():
        return f"@{username.strip()}"

    chat = message.get("chat") or {}
    return chat_display_name(chat)


def forward_summary(message: dict[str, Any]) -> str:
    origin = message.get("forward_origin")
    if isinstance(origin, dict):
        origin_type = origin.get("type")
        if origin_type == "user":
            sender_user = origin.get("sender_user") or {}
            first_name = str(sender_user.get("first_name") or "").strip()
            last_name = str(sender_user.get("last_name") or "").strip()
            full_name = " ".join(part for part in (first_name, last_name) if part)
            if full_name:
                return full_name
        if origin_type == "chat":
            sender_chat = origin.get("sender_chat") or {}
            title = sender_chat.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
        if origin_type == "channel":
            chat = origin.get("chat") or {}
            title = chat.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
        if origin_type == "hidden_user":
            hidden_name = origin.get("sender_user_name")
            if isinstance(hidden_name, str) and hidden_name.strip():
                return hidden_name.strip()

    if isinstance(message.get("forward_sender_name"), str):
        return message["forward_sender_name"].strip()
    if isinstance(message.get("forward_from_chat"), dict):
        title = message["forward_from_chat"].get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    if isinstance(message.get("forward_from"), dict):
        sender = message["forward_from"]
        first_name = str(sender.get("first_name") or "").strip()
        last_name = str(sender.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part)
        if full_name:
            return full_name

    return ""


def extract_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text.rstrip()

    caption = message.get("caption")
    if isinstance(caption, str) and caption.strip():
        return caption.rstrip()

    return ""


def cleaned_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.replace("\u200b", "").strip()
        if not line:
            continue
        line = re.sub(r"^[#>\-•◦▪·*]+\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def is_url_only(text: str) -> bool:
    return bool(re.fullmatch(r"https?://\S+", text.strip()))


def first_title_line(text: str) -> str:
    for line in cleaned_lines(text):
        if is_url_only(line):
            continue
        return line
    return ""


def extract_key_points(text: str, *, limit: int = 4) -> list[str]:
    title = first_title_line(text)
    points: list[str] = []
    seen: set[str] = set()

    for line in cleaned_lines(text):
        if line == title:
            continue
        if is_url_only(line):
            continue
        if len(line) < 6:
            continue
        normalized = line.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        points.append(line)
        if len(points) >= limit:
            break

    if points:
        return points

    if title:
        return [title]
    return []


def infer_topics(text: str, forward_from: str) -> list[str]:
    corpus = text.lower()
    topic_keywords = [
        ("반도체", ("반도체", "dram", "hbm", "nand", "파운드리", "메모리", "mlcc", "기판", "fc-bga")),
        ("광통신/CPO", ("cpo", "광통신", "광모듈", "lpo", "co-packaged", "laser", "레이저쎌")),
        ("태양광/에너지", ("태양광", "폴리실리콘", "oci", "lng", "유가", "에너지", "풍력", "태양전지")),
        ("전력/변압기", ("변압기", "배전반", "전력선", "전력", "원전", "ls electric", "초고압")),
        ("바이오/헬스케어", ("바이오", "제약", "임상", "fda", "헬스케어", "의료기기", "치료")),
        ("주주환원/밸류업", ("밸류업", "배당", "자사주", "주주환원", "소각", "주총", "pbr")),
        ("수출데이터", ("수출", "잠정", "확정치", "y/y", "m/m", "수출액", "신고가")),
        ("매크로/지정학", ("전쟁", "이란", "호르무즈", "관세", "정책", "지정학", "인플레이션")),
        ("AI/데이터센터", ("데이터센터", "gpu", "nvidia", "서버", "rubin", "groq", "llm", "추론", "ai 인프라", "ai 서버")),
    ]

    topics: list[str] = []
    for label, keywords in topic_keywords:
        if any(keyword in corpus for keyword in keywords):
            topics.append(label)
    return topics[:4]


def infer_content_type(text: str, file_specs: list[FileSpec]) -> str:
    lowered = text.lower()
    if any(spec.mime_type == "application/pdf" or spec.filename_hint.lower().endswith(".pdf") for spec in file_specs):
        return "리포트/PDF"
    if file_specs and not text.strip():
        return "차트/첨부"
    if any(spec.mime_type.startswith("image/") for spec in file_specs):
        return "차트/이미지"
    if any(keyword in lowered for keyword in ("투자의견", "목표주가", "buy", "리포트", "보고서")):
        return "리서치"
    if any(keyword in lowered for keyword in ("잠정", "확정치", "수출", "y/y", "m/m")):
        return "데이터 포인트"
    if "http" in lowered:
        return "뉴스/링크"
    return "메모/코멘트"


def one_line_summary(text: str, file_specs: list[FileSpec], topics: list[str]) -> str:
    title = first_title_line(text)
    if title:
        return title[:120]

    if any(spec.mime_type == "application/pdf" for spec in file_specs):
        return "PDF 첨부 중심 메시지"
    if file_specs:
        return "차트/이미지 첨부 중심 메시지"
    if topics:
        return f"{', '.join(topics)} 관련 메모"
    return "요약할 텍스트가 없는 메시지"


def interpret_insights(text: str, topics: list[str], content_type: str) -> list[str]:
    corpus = text.lower()
    insights: list[str] = []

    if "수출데이터" in topics:
        insights.append("수출/잠정 데이터는 업황 선행지표 성격이 강해서 관련 체인의 단기 기대치를 빠르게 움직일 수 있습니다.")
    if "주주환원/밸류업" in topics:
        insights.append("배당·자사주·밸류업 이슈는 실적 자체보다 멀티플 재평가와 수급 개선으로 연결될 가능성이 큽니다.")
    if "반도체" in topics:
        insights.append("반도체 재료는 가격(ASP), 출하, Capex, 패키징 병목 중 어느 축을 건드리는지 구분해서 보는 게 중요합니다.")
    if "광통신/CPO" in topics:
        insights.append("CPO/광통신 재료는 실제 양산 시점과 고객사 Capex 전개 속도를 함께 확인해야 의미가 커집니다.")
    if "태양광/에너지" in topics or "전력/변압기" in topics:
        insights.append("에너지/전력 재료는 정책, 증설, 프로젝트 발주, 원가 변수 중 어느 축인지 분리해서 봐야 해석이 깔끔합니다.")
    if "매크로/지정학" in topics:
        insights.append("전쟁·관세·지정학 변수는 실적보다 먼저 원가와 리스크 프리미엄에 반영될 가능성이 큽니다.")

    if any(keyword in corpus for keyword in ("가격 인상", "asp", "수요 증가", "신고가", "흑자 전환", "상향", "증가")):
        insights.append("가격 상승·수요 증가·실적 상향 표현은 단기 실적 기대를 높이는 긍정 신호로 읽을 수 있습니다.")
    if any(keyword in corpus for keyword in ("증설", "capex", "투자", "양산", "생산능력", "공장")):
        insights.append("증설/Capex는 중장기 공급 확대 신호라서 단기 모멘텀과 중장기 공급 부담을 분리해서 봐야 합니다.")
    if content_type == "리포트/PDF":
        insights.append("PDF 리포트류는 원문 보존 가치가 커서 요약만 보지 말고 첨부까지 함께 검토하는 편이 좋습니다.")

    deduped: list[str] = []
    seen: set[str] = set()
    for insight in insights:
        if insight not in seen:
            seen.add(insight)
            deduped.append(insight)
        if len(deduped) >= 3:
            break

    if deduped:
        return deduped

    return ["개별 재료보다는 출처·첨부·후속 데이터와 함께 맥락을 붙여서 보는 편이 좋습니다."]


def analyze_message(text: str, forward_from: str, file_specs: list[FileSpec]) -> MessageAnalysis:
    topics = infer_topics(text, forward_from)
    content_type = infer_content_type(text, file_specs)
    return MessageAnalysis(
        topics=topics,
        content_type=content_type,
        one_liner=one_line_summary(text, file_specs, topics),
        key_points=extract_key_points(text),
        interpretations=interpret_insights(text, topics, content_type),
    )


def guess_extension(filename_hint: str, file_path: str, mime_type: str) -> str:
    for candidate in (filename_hint, file_path):
        suffix = Path(candidate).suffix.lower()
        if suffix:
            return suffix

    mime_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "video/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
    }
    return mime_map.get(mime_type, ".bin")


def is_image(filename: str, mime_type: str) -> bool:
    if mime_type.startswith("image/"):
        return True
    return Path(filename).suffix.lower() in IMAGE_SUFFIXES


def markdown_asset_line(relative_path: Path, mime_type: str) -> str:
    target = relative_path.as_posix().replace(" ", "%20")
    filename = relative_path.name
    if is_image(filename, mime_type):
        return f"![{filename}]({target})"
    return f"[{filename}]({target})"


def should_skip_image_storage(text: str, spec: FileSpec) -> bool:
    if not is_image(spec.filename_hint, spec.mime_type):
        return False
    return "#일봉" in text or "#주봉" in text


def pick_photo_spec(message: dict[str, Any]) -> list[FileSpec]:
    photos = message.get("photo")
    if not isinstance(photos, list) or not photos:
        return []

    def photo_rank(item: dict[str, Any]) -> tuple[int, int]:
        return int(item.get("file_size") or 0), int(item.get("width") or 0) * int(item.get("height") or 0)

    best = max((item for item in photos if isinstance(item, dict) and item.get("file_id")), key=photo_rank, default=None)
    if best is None:
        return []

    return [
        FileSpec(
            kind="photo",
            file_id=str(best["file_id"]),
            filename_hint="photo.jpg",
            mime_type="image/jpeg",
        )
    ]


def extract_file_specs(message: dict[str, Any]) -> list[FileSpec]:
    specs: list[FileSpec] = []
    specs.extend(pick_photo_spec(message))

    attachment_map = {
        "document": "document",
        "video": "video",
        "animation": "animation",
        "audio": "audio",
        "voice": "voice",
        "sticker": "sticker",
    }

    for field, kind in attachment_map.items():
        item = message.get(field)
        if not isinstance(item, dict):
            continue
        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id.strip():
            continue
        filename_hint = str(item.get("file_name") or kind).strip() or kind
        mime_type = str(item.get("mime_type") or "").strip()
        if field == "sticker" and not mime_type:
            mime_type = "image/webp"
        specs.append(
            FileSpec(
                kind=kind,
                file_id=file_id,
                filename_hint=filename_hint,
                mime_type=mime_type,
            )
        )

    return specs


def ra_note_header(chat: dict[str, Any], date_key: str) -> str:
    return "\n".join(
        [
            "---",
            f'title: "{chat_display_name(chat)} | {date_key} RA"',
            'source: "Telegram Bot API"',
            f"date: {date_key}",
            "type: telegram_ra_note",
            "status: active_research",
            "topic: []",
            "tags:",
            "  - research",
            "  - telegram",
            "  - ra-note",
            "---",
            "",
            f"# {chat_display_name(chat)} | {date_key}",
            "",
            "> [!summary] TL;DR",
            "> Telegram raw capture를 당신의 투자 프레임으로 다시 정리하는 RA note입니다.",
            "",
            "> [!important] Working Rules",
            "> Fact와 interpretation을 분리하고, thesis 변화, better expression, invalidation까지 남깁니다.",
            "",
        ]
    )


def note_header(chat: dict[str, Any], date_key: str) -> str:
    return "\n".join(
        [
            f"# {chat_display_name(chat)} | {date_key}",
            "",
            "_Source: Telegram Bot API_",
            "",
            f"- chat_id: `{chat.get('id', '')}`",
            f"- chat_type: `{chat.get('type', '')}`",
            "",
        ]
    )


def relpath_for_markdown(base_dir: Path, target_path: Path) -> Path:
    return Path(os.path.relpath(target_path, start=base_dir))


def markdown_link_for_target(base_dir: Path, target_path: Path, label: str | None = None) -> str:
    relative_path = relpath_for_markdown(base_dir, target_path)
    target = relative_path.as_posix().replace(" ", "%20")
    return f"[{label or target_path.name}]({target})"


def markdown_attachment_for_target(base_dir: Path, target_path: Path, mime_type: str) -> str:
    return markdown_asset_line(relpath_for_markdown(base_dir, target_path), mime_type)


def limited_cleaned_lines(text: str, limit: int) -> list[str]:
    return cleaned_lines(text)[:limit]


def has_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def ra_status(text: str, analysis: MessageAnalysis, file_specs: list[FileSpec]) -> str:
    corpus = text.lower()
    actionable_keywords = ("목표주가", "buy", "비중확대", "매수", "re-rating", "rerating", "밸류업")
    if has_any_keyword(corpus, actionable_keywords) and analysis.key_points:
        return "actionable"
    if file_specs or len(cleaned_lines(text)) >= 3 or analysis.topics:
        return "active_research"
    return "watch"


def thesis_update_sections(text: str, analysis: MessageAnalysis) -> tuple[str, str, str]:
    corpus = text.lower()
    strengthen: list[str] = []
    weaken: list[str] = []
    observations: list[str] = []

    positive_signal = has_any_keyword(
        corpus,
        ("증가", "상승", "개선", "상향", "부족", "병목", "리드타임", "수주", "확대", "회복"),
    )
    negative_signal = has_any_keyword(
        corpus,
        ("감소", "하향", "취소", "지연", "악화", "둔화", "부진", "하락", "축소"),
    )

    if "전력/변압기" in analysis.topics:
        if has_any_keyword(corpus, ("병목", "부족", "리드타임", "지연", "전력 장비")):
            strengthen.append("전력 장비 병목 thesis는 강화 가능성이 큽니다.")
        if has_any_keyword(corpus, ("납기 정상화", "가격 하락", "증설 완료", "공급 정상화")):
            weaken.append("전력기기 공급 타이트닝 논리는 약해질 수 있습니다.")
        observations.append("데이터센터/전력망 투자에서 어떤 장비군이 가장 직접 수혜인지 다시 비교할 필요가 있습니다.")

    if "AI/데이터센터" in analysis.topics:
        if has_any_keyword(corpus, ("증설", "수요", "capex", "gpu", "ai 투자")):
            strengthen.append("AI 인프라 투자 확장 thesis는 유지되거나 강화될 수 있습니다.")
        if has_any_keyword(corpus, ("지연", "취소", "전력 부족", "병목")):
            weaken.append("데이터센터 build-out 속도에 대한 낙관은 일부 보수적으로 봐야 합니다.")
        observations.append("직접 수혜 축이 데이터센터 운영사인지, 전력 병목 공급망인지 분리해서 봐야 합니다.")

    if "반도체" in analysis.topics:
        if has_any_keyword(corpus, ("hbm", "가격 인상", "asp", "수요 증가", "증설", "패키징")):
            strengthen.append("반도체 업황 개선 또는 병목 수혜 thesis는 강화 가능성이 있습니다.")
        if has_any_keyword(corpus, ("재고", "둔화", "감산 종료", "취소", "수요 약화")):
            weaken.append("반도체 업황 탄력에 대한 기대는 일부 낮춰야 할 수 있습니다.")
        observations.append("완제품보다 병목 장비/후공정/소부장 중 어디가 더 직접적인지 다시 좁혀야 합니다.")

    if "수출데이터" in analysis.topics:
        if positive_signal:
            strengthen.append("수출 사이클 개선 thesis를 확인하는 보조 신호로 볼 수 있습니다.")
        if negative_signal:
            weaken.append("수출 회복 속도에 대한 기대는 재점검이 필요합니다.")
        observations.append("다음 잠정치와 품목/지역 breakdown까지 이어지는지 봐야 합니다.")

    if "주주환원/밸류업" in analysis.topics:
        if has_any_keyword(corpus, ("배당", "자사주", "소각", "adr", "밸류업")):
            strengthen.append("멀티플 재평가 thesis에 우호적인 자료일 수 있습니다.")
        observations.append("실제 공시와 자본배분 액션이 뒤따르는지 확인이 필요합니다.")

    if "태양광/에너지" in analysis.topics or "매크로/지정학" in analysis.topics:
        if positive_signal:
            strengthen.append("에너지 가격 또는 정책 수혜 논리가 강화될 수 있습니다.")
        if negative_signal:
            weaken.append("정책/지정학 기대만으로는 실적 연결이 약할 수 있습니다.")
        observations.append("매크로 헤드라인과 기업 실적 연결 경로를 따로 확인해야 합니다.")

    if not strengthen and positive_signal:
        strengthen.append("후속 숫자가 붙는다면 기존 thesis 강화 재료로 연결될 수 있습니다.")
    if not weaken and negative_signal:
        weaken.append("후속 데이터가 약하면 기존 thesis를 일부 낮춰야 할 수 있습니다.")
    if not observations:
        observations.append("기존 종목/산업 노트와 대조해 thesis 강화·약화 여부를 다시 확인해야 합니다.")

    return (
        " ".join(strengthen[:2]),
        " ".join(weaken[:2]),
        " ".join(observations[:2]),
    )


def better_expression_sections(text: str, analysis: MessageAnalysis) -> tuple[str, str, str]:
    corpus = text.lower()

    if "전력/변압기" in analysis.topics and "AI/데이터센터" in analysis.topics:
        better = "데이터센터 운영사 자체보다 변압기·배전반·현장발전 공급망이 더 직접적인 표현일 수 있습니다."
    elif "반도체" in analysis.topics:
        better = "완제품보다 병목 장비·후공정·대장주가 더 단순한 표현일 수 있습니다."
    elif "수출데이터" in analysis.topics:
        better = "데이터 민감도가 높은 대장주나 ETF가 개별 중소형주보다 나은 표현일 수 있습니다."
    elif "주주환원/밸류업" in analysis.topics:
        better = "개별 이벤트보다 자사주·배당 정책이 명확한 대형주/지주사 비교가 먼저입니다."
    elif "매크로/지정학" in analysis.topics:
        better = "헤드라인 수혜주보다 실제 가격·물류·정책 전이의 직접 수혜자 비교가 먼저입니다."
    else:
        better = "현재 단계에서는 테마 확인이 우선이고, 표현 종목은 대장주/ETF와 먼저 비교해야 합니다."

    if has_any_keyword(corpus, ("2등주", "테마주", "급등", "신규 상장")):
        hold = "중소형 2등주로 바로 점프하지 말고 대장주/ETF를 먼저 비교하는 편이 안전합니다."
    else:
        hold = "섹터는 맞아도 표현 종목이 틀릴 수 있으니, 현재 종목 고정을 서두르지 않는 편이 좋습니다."

    avoid = "내 언어로 5문장 thesis와 1차 stop(price/thesis/time) 없이 신규 진입하거나 추격하지 않는 것이 좋습니다."
    return better, hold, avoid


def do_not_misread_lines(text: str, analysis: MessageAnalysis, file_specs: list[FileSpec], forward_from: str) -> list[str]:
    warnings: list[str] = []
    corpus = text.lower()

    if forward_from:
        warnings.append("포워드 메시지는 borrowed thesis가 되기 쉬우니, 내 언어로 다시 쓰기 전까지 투자 아이디어로 확정하지 마십시오.")
    if file_specs:
        warnings.append("이미지/PDF 첨부는 일부만 읽고 결론 내리기 쉬우니 원문과 숫자를 다시 확인해야 합니다.")
    if has_any_keyword(corpus, ("급등", "신고가", "강세", "재평가")):
        warnings.append("강한 톤만 보고 놓친 뒤 추격하는 패턴으로 연결되지 않도록 주의해야 합니다.")
    if not warnings:
        warnings.append("사실과 해석을 섞지 말고, 후속 숫자 확인 전에는 강한 결론으로 점프하지 않는 편이 좋습니다.")
    return warnings[:3]


def counterarguments_for_message(text: str, analysis: MessageAnalysis, file_specs: list[FileSpec]) -> list[str]:
    counterarguments: list[str] = []
    corpus = text.lower()

    if "AI/데이터센터" in analysis.topics and "전력/변압기" in analysis.topics:
        counterarguments.append("전력 병목이 실제 장비사 마진 확대로 바로 연결되지 않고, 프로젝트 지연만 만들 수도 있습니다.")
    if "반도체" in analysis.topics:
        counterarguments.append("반도체 재료가 이미 주가에 선반영됐거나, 실제 실적 연결이 생각보다 느릴 수 있습니다.")
    if "매크로/지정학" in analysis.topics:
        counterarguments.append("헤드라인 충격은 크지만, 기업 실적에 남는 효과는 생각보다 짧을 수 있습니다.")
    if file_specs:
        counterarguments.append("첨부 위주 메시지는 원문 맥락이 빠져 있을 수 있어 과잉 해석 위험이 있습니다.")
    if has_any_keyword(corpus, ("증설", "capex", "투자 확대")):
        counterarguments.append("증설 뉴스는 단기 모멘텀보다 중장기 공급 부담으로 되돌아올 수 있습니다.")
    if not counterarguments:
        counterarguments.append("현재 메시지만으로는 실적과 밸류 재평가를 바로 연결하기 어렵습니다.")
    return counterarguments[:3]


def next_checkpoints_for_message(text: str, analysis: MessageAnalysis, file_specs: list[FileSpec]) -> list[str]:
    checkpoints: list[str] = []

    if "전력/변압기" in analysis.topics:
        checkpoints.append("관련 업체의 수주잔고, 리드타임, 증설 일정이 다음 분기에도 유지되는지 확인")
    if "AI/데이터센터" in analysis.topics:
        checkpoints.append("데이터센터 Capex, 지연/취소 비중, 전력 확보 일정이 공시/컨콜로 확인되는지 점검")
    if "반도체" in analysis.topics:
        checkpoints.append("ASP, 출하, HBM/후공정 Capex, 고객사 가이던스 변화를 추적")
    if "수출데이터" in analysis.topics:
        checkpoints.append("다음 잠정/확정 수출과 품목·지역 breakdown에서 같은 방향성이 반복되는지 확인")
    if "주주환원/밸류업" in analysis.topics:
        checkpoints.append("자사주·배당·ADR 등 실제 공시 액션이 뒤따르는지 점검")
    if any(spec.mime_type == "application/pdf" for spec in file_specs):
        checkpoints.append("첨부 PDF 원문에서 숫자 가정과 밸류 프레임을 직접 확인")
    if not checkpoints:
        checkpoints.append("관련 종목의 다음 실적, 공시, 컨콜에서 이번 메시지의 주장과 연결되는 숫자를 확인")
    return checkpoints[:3]


def invalidation_lines(text: str, analysis: MessageAnalysis) -> list[str]:
    invalidations: list[str] = []
    corpus = text.lower()

    if "전력/변압기" in analysis.topics and has_any_keyword(corpus, ("병목", "부족", "리드타임")):
        invalidations.append("납기 정상화나 공급 확대가 확인되면 병목 프리미엄 논리는 약해질 수 있습니다.")
    if "AI/데이터센터" in analysis.topics and has_any_keyword(corpus, ("지연", "취소", "전력 부족")):
        invalidations.append("다음 분기 Capex 재가속과 프로젝트 재개가 확인되면 부정 해석은 약해질 수 있습니다.")
    if "반도체" in analysis.topics:
        invalidations.append("후속 실적에서 ASP·출하·수주가 따라오지 않으면 업황 개선 해석은 약화됩니다.")
    if "수출데이터" in analysis.topics:
        invalidations.append("다음 잠정치에서 방향성이 뒤집히면 이번 데이터 해석은 재검토가 필요합니다.")
    if not invalidations:
        invalidations.append("후속 공시·실적·데이터에서 숫자 확인이 안 되면 이번 해석은 약화됩니다.")
    return invalidations[:2]


def callout_block(kind: str, title: str, lines: list[str]) -> str:
    rendered = [f"> [!{kind}] {title}"]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            rendered.append(">")
        else:
            rendered.append(f"> {stripped}")
    return "\n".join(rendered)


def ra_message_block(
    *,
    message_key: str,
    timestamp: str,
    sender: str,
    forward_from: str,
    text: str,
    analysis: MessageAnalysis,
    file_specs: list[FileSpec],
    attachments: list[tuple[Path, str]],
    raw_note_path: Path,
    ra_note_path: Path,
) -> str:
    status = ra_status(text, analysis, file_specs)
    what_matters = analysis.key_points[:3] or limited_cleaned_lines(text, 3) or [analysis.one_liner]
    facts = analysis.key_points[:4] or limited_cleaned_lines(text, 4)
    strengthen, weaken, observation = thesis_update_sections(text, analysis)
    better, hold, avoid = better_expression_sections(text, analysis)
    warnings = do_not_misread_lines(text, analysis, file_specs, forward_from)
    counterarguments = counterarguments_for_message(text, analysis, file_specs)
    checkpoints = next_checkpoints_for_message(text, analysis, file_specs)
    invalidations = invalidation_lines(text, analysis)

    source_lines = [
        f"- raw note: {markdown_link_for_target(ra_note_path.parent, raw_note_path, raw_note_path.name)}",
    ]
    if forward_from:
        source_lines.append(f"- forwarded_from: `{forward_from}`")
    for attachment_path, mime_type in attachments:
        source_lines.append(f"- {markdown_attachment_for_target(ra_note_path.parent, attachment_path, mime_type)}")

    lines = [
        f"## {timestamp} | {sender}",
        f"- message_key: `{message_key}`",
        f"- status: `{status}`",
    ]
    if analysis.topics:
        lines.append(f"- topic: {', '.join(analysis.topics)}")
    lines.append("")
    lines.append(callout_block("summary", "TL;DR", [analysis.one_liner]))
    lines.append("")
    lines.append(callout_block("important", "What Matters", [f"- {item}" for item in what_matters]))
    lines.append("")
    lines.append(callout_block("tip", "My Take", [item for item in analysis.interpretations[:3]]))
    lines.append("")
    lines.append(callout_block("warning", "Do Not Misread", [f"- {item}" for item in warnings]))
    lines.append("")
    lines.append("### ==핵심 인사이트==")
    for item in what_matters:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("### Facts That Matter")
    for item in facts:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("### My View")
    lines.append(f"- 기존 thesis 강화: {strengthen or '관련 thesis와 대조 필요'}")
    lines.append(f"- 기존 thesis 약화: {weaken or '현재 메시지만으로는 직접 약화 근거가 제한적입니다.'}")
    lines.append(f"- 새로 생긴 관찰 포인트: {observation}")
    lines.append("")
    lines.append("### Better Expression")
    lines.append(f"- 더 좋은 표현: {better}")
    lines.append(f"- 보류할 표현: {hold}")
    lines.append(f"- 지금 하면 안 되는 행동: {avoid}")
    lines.append("")
    lines.append("### Counterarguments")
    for item in counterarguments:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("### Next Checkpoints")
    for item in checkpoints:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("### Invalidation")
    for item in invalidations:
        lines.append(f"- {item}")
    if text:
        lines.extend(["", "### Source Trail", *source_lines, "", "### Original Text", "", text])
    else:
        lines.extend(["", "### Source Trail", *source_lines])
    lines.append("")
    return "\n".join(lines)


def message_block(
    *,
    message_key: str,
    timestamp: str,
    sender: str,
    forward_from: str,
    text: str,
    analysis: MessageAnalysis,
    attachments: list[str],
) -> str:
    lines = [
        f"## {timestamp} | {sender}",
        f"- message_key: `{message_key}`",
    ]

    if forward_from:
        lines.append(f"- forwarded_from: `{forward_from}`")

    if analysis.topics:
        lines.append(f"- 항목: {', '.join(analysis.topics)}")
    lines.append(f"- 유형: {analysis.content_type}")
    lines.append(f"- 요약: {analysis.one_liner}")

    for index, insight in enumerate(analysis.interpretations, start=1):
        lines.append(f"- 해석 {index}: {insight}")

    for index, point in enumerate(analysis.key_points, start=1):
        lines.append(f"- 핵심 {index}: {point}")

    if text:
        lines.extend(["", "원문:", "", text])

    if attachments:
        lines.extend(["", "Attachments:"])
        for attachment in attachments:
            lines.append(f"- {attachment}")

    lines.append("")
    return "\n".join(lines)


def ensure_note_exists(note_path: Path, header: str) -> str:
    if note_path.exists():
        return note_path.read_text(encoding="utf-8")

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(header, encoding="utf-8")
    return header


def append_block_if_missing(note_path: Path, header: str, message_key: str, block: str) -> bool:
    current_content = ensure_note_exists(note_path, header)
    if f"`{message_key}`" in current_content:
        return False
    separator = "" if current_content.endswith("\n\n") else "\n"
    note_path.write_text(f"{current_content}{separator}{block}", encoding="utf-8")
    return True


def store_ra_message(
    *,
    chat: dict[str, Any],
    date_key: str,
    dt: datetime,
    message_key: str,
    sender: str,
    forward_from: str,
    text: str,
    analysis: MessageAnalysis,
    file_specs: list[FileSpec],
    attachment_paths: list[tuple[Path, str]],
    raw_note_path: Path,
    output_root: Path,
) -> None:
    slug = chat_slug(chat)
    ra_dir = output_root / "ra" / slug / date_key[:4] / date_key[5:7]
    ra_note_path = ra_dir / f"{date_key}.md"
    block = ra_message_block(
        message_key=message_key,
        timestamp=dt.strftime("%H:%M:%S"),
        sender=sender,
        forward_from=forward_from,
        text=text,
        analysis=analysis,
        file_specs=file_specs,
        attachments=attachment_paths,
        raw_note_path=raw_note_path,
        ra_note_path=ra_note_path,
    )
    append_block_if_missing(
        ra_note_path,
        ra_note_header(chat, date_key),
        message_key,
        block,
    )


def store_message(
    *,
    message: dict[str, Any],
    output_root: Path,
    timezone: ZoneInfo,
    resolve_file_path_fn: Callable[[str], str],
    download_file_fn: Callable[[str, Path], None],
) -> bool:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return False

    date_unix = message.get("date")
    if not isinstance(date_unix, int):
        return False

    dt = datetime.fromtimestamp(date_unix, tz=timezone)
    date_key = dt.date().isoformat()
    slug = chat_slug(chat)
    note_dir = output_root / "bot" / slug / date_key[:4] / date_key[5:7]
    note_path = note_dir / f"{date_key}.md"
    asset_dir = note_dir / f"{date_key}_assets"
    message_key = f"{chat.get('id', 'unknown')}:{message.get('message_id', 'unknown')}"
    text = extract_text(message)
    file_specs = extract_file_specs(message)
    sender = sender_name(message)
    forward_from = forward_summary(message)
    analysis = analyze_message(text, forward_from, file_specs)
    attachment_lines: list[str] = []
    attachment_paths: list[tuple[Path, str]] = []
    for index, spec in enumerate(file_specs, start=1):
        if should_skip_image_storage(text, spec):
            continue
        remote_path = resolve_file_path_fn(spec.file_id)
        extension = guess_extension(spec.filename_hint, remote_path, spec.mime_type)
        hint_stem = sanitize_filename(Path(spec.filename_hint).stem or spec.kind)
        destination_name = f"{message.get('message_id', 'msg')}_{index}_{hint_stem}{extension}"
        destination = asset_dir / destination_name
        if not destination.exists():
            download_file_fn(remote_path, destination)
        relative_path = Path(f"./{date_key}_assets") / destination.name
        attachment_lines.append(markdown_asset_line(relative_path, spec.mime_type))
        attachment_paths.append((destination, spec.mime_type))

    raw_block = message_block(
        message_key=message_key,
        timestamp=dt.strftime("%H:%M:%S"),
        sender=sender,
        forward_from=forward_from,
        text=text,
        analysis=analysis,
        attachments=attachment_lines,
    )
    raw_stored = append_block_if_missing(
        note_path,
        note_header(chat, date_key),
        message_key,
        raw_block,
    )

    store_ra_message(
        chat=chat,
        date_key=date_key,
        dt=dt,
        message_key=message_key,
        sender=sender,
        forward_from=forward_from,
        text=text,
        analysis=analysis,
        file_specs=file_specs,
        attachment_paths=attachment_paths,
        raw_note_path=note_path,
        output_root=output_root,
    )

    if raw_stored:
        manifest_record = RawManifestRecord(
            source_type="telegram_bot",
            source_path=str(note_path.resolve()),
            message_key=message_key,
            date=date_key,
            sender=sender,
            forwarded_from=forward_from or None,
            attachments=[str(path.resolve()) for path, _ in attachment_paths],
            hash=sha256_text(message_key, sender, forward_from, text, "".join(attachment_lines)),
        )
        append_jsonl_record(
            manifest_path_for_note(note_path),
            manifest_record,
            dedupe_key="message_key",
        )
    return raw_stored


def process_updates(
    *,
    updates: list[dict[str, Any]],
    output_root: Path,
    timezone: ZoneInfo,
    state_path: Path,
    state: dict[str, Any],
    resolve_file_path_fn: Callable[[str], str],
    download_file_fn: Callable[[str, Path], None],
) -> int:
    processed_count = 0
    last_message_ids = state.setdefault("last_message_id_by_chat", {})

    for update in updates:
        update_id = update.get("update_id")
        message = update.get("message")
        if isinstance(message, dict):
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            chat_id = chat.get("id")
            message_id = message.get("message_id")
            if isinstance(chat_id, int) and isinstance(message_id, int):
                chat_key = str(chat_id)
                previous_message_id = last_message_ids.get(chat_key)
                if isinstance(previous_message_id, int) and message_id > previous_message_id + 1:
                    gap_start = previous_message_id + 1
                    gap_end = message_id - 1
                    gap_size = gap_end - gap_start + 1
                    warning = (
                        f"[telegram-bot-ingest] Detected message_id gap for chat {chat_key}: "
                        f"{gap_start}-{gap_end} (size={gap_size}). "
                        "These messages were not present in the fetched Bot API updates."
                    )
                    print(warning, file=sys.stderr)
                    append_gap_audit(
                        output_root,
                        {
                            "detected_at": datetime.now(tz=timezone).isoformat(),
                            "chat_id": chat_id,
                            "chat_name": chat_display_name(chat),
                            "previous_message_id": previous_message_id,
                            "current_message_id": message_id,
                            "gap_start": gap_start,
                            "gap_end": gap_end,
                            "gap_size": gap_size,
                            "state_offset_at_detection": int(state.get("offset") or 0),
                            "note": (
                                "Gap detected while processing fresh Bot API updates. "
                                "Likely causes: old pending updates expired before polling, "
                                "messages never reached the bot chat, or historical offset loss."
                            ),
                        },
                    )
                last_message_ids[chat_key] = max(int(previous_message_id or 0), message_id)

            stored = store_message(
                message=message,
                output_root=output_root,
                timezone=timezone,
                resolve_file_path_fn=resolve_file_path_fn,
                download_file_fn=download_file_fn,
            )
            if stored:
                processed_count += 1

        if isinstance(update_id, int):
            state["offset"] = max(int(state.get("offset") or 0), update_id + 1)
            save_state(state_path, state)

    return processed_count


def main() -> None:
    args = parse_args()
    env_file = args.env_file.expanduser().resolve()
    load_dotenv(env_file)
    token = require_token()
    output_root = args.output_root.expanduser().resolve()
    timezone = ZoneInfo(args.timezone)
    state_path = (
        args.state_file.expanduser().resolve()
        if args.state_file
        else output_root / "state" / DEFAULT_STATE_FILE
    )
    state = load_state(state_path)
    bootstrapped_last_ids = bootstrap_last_message_ids(output_root)
    state_last_ids = state.get("last_message_id_by_chat")
    if not isinstance(state_last_ids, dict):
        state_last_ids = {}
    reconciled_last_ids: dict[str, int] = {}
    for chat_key, message_id in state_last_ids.items():
        try:
            reconciled_last_ids[str(chat_key)] = int(message_id)
        except (TypeError, ValueError):
            continue
    for chat_key, message_id in bootstrapped_last_ids.items():
        reconciled_last_ids[chat_key] = max(reconciled_last_ids.get(chat_key, 0), message_id)
    if state.get("last_message_id_by_chat") != reconciled_last_ids:
        state["last_message_id_by_chat"] = reconciled_last_ids
        save_state(state_path, state)

    def resolve_remote_path(file_id: str) -> str:
        return resolve_file_path(token, file_id)

    def download_remote_file(file_path: str, destination: Path) -> None:
        download_file(token, file_path, destination)

    total_processed = 0
    stale_poll_warning_shown = False

    while True:
        try:
            poll_started_at = datetime.now(tz=timezone)
            last_successful_poll_at = parse_iso_datetime(state.get("last_successful_poll_at"))
            if (
                not stale_poll_warning_shown
                and last_successful_poll_at is not None
                and poll_started_at - last_successful_poll_at > timedelta(hours=STALE_POLL_WARNING_HOURS)
            ):
                elapsed = poll_started_at - last_successful_poll_at
                hours = elapsed.total_seconds() / 3600
                print(
                    "[telegram-bot-ingest] Warning: the last successful poll was "
                    f"{hours:.1f} hours ago at {last_successful_poll_at.isoformat()}. "
                    f"Telegram Bot API pending updates are typically only retained for up to {BOT_API_RETENTION_HOURS} hours, "
                    "so older messages may already be unavailable.",
                    file=sys.stderr,
                )
                stale_poll_warning_shown = True

            fetch_offset = int(state.get("offset") or 0)
            poll_timeout = args.poll_timeout if args.watch else 0
            updates = get_updates(token, fetch_offset, poll_timeout)
            if updates:
                update_ids = [
                    update.get("update_id")
                    for update in updates
                    if isinstance(update.get("update_id"), int)
                ]
                message_ids = [
                    update.get("message", {}).get("message_id")
                    for update in updates
                    if isinstance(update.get("message"), dict)
                    and isinstance(update["message"].get("message_id"), int)
                ]
                update_span = ""
                if update_ids:
                    update_span = f", update_id {min(update_ids)}-{max(update_ids)}"
                message_span = ""
                if message_ids:
                    message_span = f", message_id {min(message_ids)}-{max(message_ids)}"
                print(
                    f"[telegram-bot-ingest] Fetched {len(updates)} update(s) from offset {fetch_offset}"
                    f"{update_span}{message_span}.",
                    file=sys.stderr,
                )
            append_raw_updates(
                output_root=output_root,
                timezone=timezone,
                fetch_offset=fetch_offset,
                updates=updates,
            )
            batch_processed = process_updates(
                updates=updates,
                output_root=output_root,
                timezone=timezone,
                state_path=state_path,
                state=state,
                resolve_file_path_fn=resolve_remote_path,
                download_file_fn=download_remote_file,
            )
            total_processed += batch_processed
            if updates:
                print(
                    f"[telegram-bot-ingest] Stored {batch_processed} new message(s) in this batch "
                    f"(total so far: {total_processed}).",
                    file=sys.stderr,
                )
            state["last_successful_poll_at"] = datetime.now(tz=timezone).isoformat()
            state["last_polled_update_count"] = len(updates)
            save_state(state_path, state)
            if not args.watch and not updates:
                break
        except (RuntimeError, subprocess.CalledProcessError, TimeoutError) as exc:
            if not args.watch:
                print(f"[telegram-bot-ingest] {exc}", file=sys.stderr)
                raise SystemExit(1) from exc
            print(f"[telegram-bot-ingest] {exc}", file=sys.stderr)
            time.sleep(5)
        except KeyboardInterrupt:
            break

    print(
        f"Processed {total_processed} new message(s). State file: {state_path}",
        file=sys.stdout,
    )


if __name__ == "__main__":
    main()
