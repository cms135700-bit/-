from __future__ import annotations

import argparse
from pathlib import Path

from .indexer import reindex_vault
from .publisher import publish_artifact
from .report_pipeline import extract_pdf_to_card
from .router import route_to_spec
from .synthesis import synthesize_to_brief
from .telegram_pipeline import (
    normalize_note_to_packet,
    parse_telegram_note,
    write_manifest_records,
)
from .verifier import write_verification


def telegram_note_paths(root: Path) -> list[Path]:
    return sorted(root.glob("telegram/bot/**/*.md")) + sorted(root.glob("telegram/chats/**/*.md"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Knowledge pipeline utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("telegram-backfill-manifests")
    backfill.add_argument("--vault-root", type=Path, default=Path.cwd())

    normalize = subparsers.add_parser("telegram-normalize-day")
    normalize.add_argument("note_path", type=Path)
    normalize.add_argument("--output", type=Path)

    report = subparsers.add_parser("report-extract")
    report.add_argument("pdf_path", type=Path)
    report.add_argument("--output", type=Path)

    synth = subparsers.add_parser("synthesize")
    synth.add_argument("artifact_paths", nargs="+", type=Path)
    synth.add_argument("--output", type=Path)

    route = subparsers.add_parser("route")
    route.add_argument("artifact_path", type=Path)
    route.add_argument("--vault-root", type=Path, default=Path.cwd())
    route.add_argument("--output", type=Path)

    publish = subparsers.add_parser("publish")
    publish.add_argument("artifact_path", type=Path)
    publish.add_argument("--spec", required=True, type=Path)
    publish.add_argument("--verification-report", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("artifact_path", type=Path)
    verify.add_argument("--note", required=True, type=Path)
    verify.add_argument("--output", type=Path)

    reindex = subparsers.add_parser("reindex")
    reindex.add_argument("--vault-root", type=Path, default=Path.cwd())

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "telegram-backfill-manifests":
        written = 0
        for note_path in telegram_note_paths(args.vault_root.resolve()):
            messages = parse_telegram_note(note_path)
            if not messages:
                continue
            write_manifest_records(note_path, messages)
            written += 1
        print(f"backfilled raw_manifest for {written} telegram note(s)")
        return

    if args.command == "telegram-normalize-day":
        destination = normalize_note_to_packet(args.note_path.resolve(), args.output.resolve() if args.output else None)
        print(destination)
        return

    if args.command == "report-extract":
        destination = extract_pdf_to_card(args.pdf_path.resolve(), args.output.resolve() if args.output else None)
        print(destination)
        return

    if args.command == "synthesize":
        destination = synthesize_to_brief(
            [path.resolve() for path in args.artifact_paths],
            args.output.resolve() if args.output else None,
        )
        print(destination)
        return

    if args.command == "route":
        destination = route_to_spec(
            args.artifact_path.resolve(),
            args.vault_root.resolve(),
            args.output.resolve() if args.output else None,
        )
        print(destination)
        return

    if args.command == "publish":
        destination = publish_artifact(
            args.artifact_path.resolve(),
            args.spec.resolve(),
            args.verification_report.resolve() if args.verification_report else None,
        )
        print(destination)
        return

    if args.command == "verify":
        destination = write_verification(
            args.note.resolve(),
            args.artifact_path.resolve(),
            args.output.resolve() if args.output else None,
        )
        print(destination)
        return

    if args.command == "reindex":
        index_path, log_path = reindex_vault(args.vault_root.resolve())
        print(index_path)
        print(log_path)
        return


if __name__ == "__main__":
    main()
