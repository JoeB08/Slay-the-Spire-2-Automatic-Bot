"""Write a readable story for every run in a folder.

    python scripts/run_story.py [FOLDER]

FOLDER holds a `runs.jsonl` and the decision logs it names -- `logs/` (the
default) or any set archived under `stats/archive/`. Stories and an INDEX.md
go to `FOLDER/stories/`, rebuilt from scratch each time. The bot writes the
same stories itself as each run ends; this is for past sets, or to rebuild.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import log_io, run_story  # noqa: E402


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs"
    runs_file = folder / "runs.jsonl"
    if not runs_file.exists():
        print(f"No runs.jsonl in {folder}")
        return
    out = folder / run_story.STORIES_DIR
    if out.exists():
        shutil.rmtree(out)
    # utf-8-sig: a runs.jsonl re-saved by Windows PowerShell starts with a BOM,
    # which would otherwise break the first line.
    records = [json.loads(line) for line in runs_file.read_text(encoding="utf-8-sig").splitlines()
               if line.strip()]
    for record in records:
        name = record.get("decision_log") or ""
        log = (log_io.find_log(folder / name)
               or log_io.find_log(ROOT / "logs" / name)
               or folder / name)
        path = run_story.write_story(log, record, out)
        print(f"wrote {path.name}")
    print(f"{len(records)} stories -> {out}")


if __name__ == "__main__":
    main()
