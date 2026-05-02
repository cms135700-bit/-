# Knowledge Pipeline

`knowledge_pipeline` is a v1 artifact-based workflow for Telegram messages and PDF reports.

The goal is not summary automation. The goal is preserving provenance, decomposing thesis logic, and routing approved insights into the Obsidian knowledge base without losing source traceability.

## Artifact flow

```text
raw_manifest
  -> evidence_packet / thesis_card
  -> insight_brief
  -> publish_spec
  -> verification_report
```

Rules:

- Telegram raw notes remain immutable.
- Raw Telegram/PDF sources do not update evergreen notes directly.
- Long-lived notes should be checked by `verification_report` before merge.

## Commands

Backfill raw manifests for existing Telegram bot/export notes:

```bash
python3 -m knowledge_pipeline telegram-backfill-manifests --vault-root /Users/ppingkku/Documents/stock
```

Normalize one Telegram day note into an `evidence_packet`:

```bash
python3 -m knowledge_pipeline telegram-normalize-day \
  /Users/ppingkku/Documents/stock/telegram/bot/cms-6746975543/2026/03/2026-03-25.md
```

Extract one PDF into a `thesis_card`:

```bash
python3 -m knowledge_pipeline report-extract \
  /Users/ppingkku/Documents/stock/리포트/무제\ 폴더/비나텍_기업리포트_260303.pdf
```

Synthesize one or more artifacts into an `insight_brief`:

```bash
python3 -m knowledge_pipeline synthesize artifact1.json artifact2.json
```

Route one artifact into a deterministic `publish_spec`:

```bash
python3 -m knowledge_pipeline route artifact.json --vault-root /Users/ppingkku/Documents/stock
```

Apply one `publish_spec` to a note destination:

```bash
python3 -m knowledge_pipeline publish artifact.json \
  --spec artifact.publish_spec.json \
  --verification-report artifact.verification_report.json
```

Rebuild the root wiki index and initialize the append-only log if needed:

```bash
python3 -m knowledge_pipeline reindex --vault-root /Users/ppingkku/Documents/stock
```

Verify whether a note update is safe:

```bash
python3 -m knowledge_pipeline verify artifact.json \
  --note /Users/ppingkku/Documents/stock/기업/씨에스윈드.md
```

## Current scope

- `raw_manifest`: deterministic worker output for Telegram bot/export capture.
- `evidence_packet`: Telegram day normalization with source refs and extracted numbers.
- `thesis_card`: PDF text extraction + minimal thesis decomposition.
- `insight_brief`: deterministic synthesis skeleton for higher-level mechanism work.
- `publish_spec`: rule-based primary/related note routing.
- `publish`: primary target publish + safe related target fan-out + index/log side effects.
- `verification_report`: duplicate/contradiction/staleness pre-check.
- `reindex`: root `index.md` regeneration and `log.md` bootstrap.

## Limitations

- Image OCR is not bundled in v1. Image/PDF OCR gaps are explicitly surfaced in `image_read_limits`.
- `insight_brief` is a structured synthesis scaffold, not a full discretionary analyst write-up.
- `verification_report` is heuristic and should be treated as a guardrail, not final truth.
- Existing company/industry notes require a compatible `verification_report` before `publish` will append.
