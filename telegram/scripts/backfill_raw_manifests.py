#!/usr/bin/env python3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_pipeline.cli import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "telegram-backfill-manifests", *sys.argv[1:]]
    main()
