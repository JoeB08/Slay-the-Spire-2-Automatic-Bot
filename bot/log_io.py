"""Reading the decision logs, compressed or not.

A 20-run set writes ~60 MB of `run_*.jsonl`, and the archive of every set ever
measured had grown past 300 MB -- all of it text, all of it about 95% air. So
`scripts/compress_logs.py` gzips finished logs in place, and everything that
reads one goes through here instead of calling `open()` directly.

Three details keep that transparent:

* `log_glob()` matches `X.jsonl` and `X.jsonl.gz` alike, and sorts on the name
  **without** the suffix. `decision_snapshot.py` and `analysis/replay_compare.py`
  number payloads by their position in the file list and compare two runs index
  by index, so a compressed archive has to be iterated in exactly the order the
  uncompressed one was.
* `open_log()` decompresses on the fly and reads `utf-8-sig`: a log re-saved by
  Windows PowerShell starts with a BOM, which otherwise lands on the first
  line's `{` and breaks the JSON parse of that one row only -- easy to miss.
* Empty logs are never compressed (gzip would give a 0-byte file a header and a
  non-zero size), so the `st_size > 0` filters several scripts use to skip the
  logs of processes that never acted keep working unchanged.

Nothing here writes. The bot's own logging still opens its file directly, so a
log being appended to is always plain text and readable with any tool.
"""
from __future__ import annotations

import glob as _glob
import gzip
import json
from pathlib import Path
from typing import Any, Iterator, Optional, TextIO, Union

SUFFIX = ".gz"

StrPath = Union[str, Path]


def is_compressed(path: StrPath) -> bool:
    return str(path).endswith(SUFFIX)


def open_log(path: StrPath, errors: str = "ignore") -> TextIO:
    """A text handle on a log, compressed or not."""
    if is_compressed(path):
        return gzip.open(path, "rt", encoding="utf-8-sig", errors=errors)
    return open(path, "rt", encoding="utf-8-sig", errors=errors)


def sort_key(path: StrPath) -> str:
    """Order by the uncompressed name, so compressing changes no ordering."""
    text = str(path)
    return text[: -len(SUFFIX)] if text.endswith(SUFFIX) else text


def display_name(path: StrPath) -> str:
    """A log's name without the .gz, for printing and for grouping by run.

    Reports name the log a finding came from, and that name is quoted back into
    fixtures and notes -- so it has to read the same whether the log happens to
    be packed or not.
    """
    return Path(sort_key(path)).name


def log_glob(pattern: StrPath, recursive: bool = False) -> list[str]:
    """`glob.glob`, also matching the compressed form of every hit."""
    pat = str(pattern)
    found = set(_glob.glob(pat, recursive=recursive))
    found |= set(_glob.glob(pat + SUFFIX, recursive=recursive))
    return sorted(found, key=sort_key)


def log_paths(root: Path, pattern: str) -> list[Path]:
    """`Path.glob`, also matching the compressed form of every hit."""
    found = set(root.glob(pattern)) | set(root.glob(pattern + SUFFIX))
    return sorted(found, key=sort_key)


def find_log(path: StrPath) -> Optional[Path]:
    """`path` if it exists, else its compressed form, else None.

    For resolving a decision-log *name* -- `runs.jsonl` records the log each run
    was written to, and that log may since have been compressed.
    """
    plain = Path(path)
    if plain.exists():
        return plain
    packed = Path(str(plain) + SUFFIX)
    return packed if packed.exists() else None


def rows(path: StrPath) -> Iterator[dict[str, Any]]:
    """Every JSON row in a log, skipping blank and unparseable lines.

    A log is truncated mid-line whenever the bot process is killed, which is how
    most sets end, so the last line is routinely half-written.
    """
    with open_log(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue
