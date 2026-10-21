"""Merge translation entries into app/i18n/locales/{en,ko,pl}.json.

    python scripts/i18n_merge.py path/to/entries.py

The entries file defines ``ENTRIES = {"key": ("English", "한국어", "Polski"), ...}``.
Existing keys are overwritten, new keys are appended; all three catalogs stay in
step (tests/test_i18n.py checks key and placeholder parity).
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

LOCALES = Path(__file__).resolve().parent.parent / "app" / "i18n" / "locales"


def merge(entries: dict[str, tuple[str, str, str]]) -> None:
    for idx, lang in enumerate(("en", "ko", "pl")):
        path = LOCALES / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, texts in entries.items():
            data[key] = texts[idx]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    merge(runpy.run_path(sys.argv[1])["ENTRIES"])
