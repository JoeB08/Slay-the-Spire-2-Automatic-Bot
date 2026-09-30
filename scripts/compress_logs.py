"""Gzip finished decision logs in place.

    python scripts/compress_logs.py --dry-run     # what it would save
    python scripts/compress_logs.py               # archives + logs/, keeping the newest
    python scripts/compress_logs.py --all         # include the newest log too
    python scripts/compress_logs.py --decompress  # put it all back

Why
---
The decision logs are the project's raw evidence and are never thrown away:
every archived set is replayed by `decision_snapshot.py` to prove a refactor
changed no behaviour, so deleting them would cost the only real regression test
this project has. They are also JSON text with one line per decision, repeating
the same field names a few hundred thousand times, and gzip takes about 95% of
that back.

What it does not touch
----------------------
* `runs.jsonl` -- one line per finished run, a few hundred KB in total, and the
  index every report and analysis script starts from. Nothing is gained by
  packing it and everything that reads a set would need a decompress step.
* `*.md` -- the reports and per-run stories. All the markdown in the project
  comes to under 1 MB of 400, and it is the part a person actually reads, so it
  stays as plain text that opens in anything.
* `bot_stdout.log` -- small, and the first thing to read when a set dies.
* Empty logs, left plain so the `st_size > 0` filters that skip the logs of
  processes which never acted keep working.
* The newest `logs/run_*.jsonl` unless `--all`: that is the file a running bot
  is appending to. On Windows the delete would fail anyway, but skipping it
  means never having to think about it.

Safety: each file is compressed to a temporary name, read back, and compared by
SHA-256 against the original. The original is deleted only if the hashes match,
so an interrupted or corrupt write can never lose a log.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything worth packing. `runs.jsonl` is deliberately absent -- see above.
PATTERNS = (
    "stats/archive/**/run_*.jsonl",
    "stats/archive/**/extra_run_*.jsonl",
    "stats/archive/**/shadow_*.jsonl",
    "stats/archive/**/replay_*.jsonl",
    "logs/run_*.jsonl",
    "logs/shadow_*.jsonl",
)
CHUNK = 1 << 20


def _digest(fh) -> str:
    h = hashlib.sha256()
    while chunk := fh.read(CHUNK):
        h.update(chunk)
    return h.hexdigest()


def _targets(include_newest: bool) -> tuple[list[Path], int]:
    """The logs worth packing, and how many empty ones were passed over.

    Analysis scripts used to open a decision log just by importing `bot.loop`,
    so the archive is littered with zero-byte stubs -- about three quarters of
    the files in it. They are left plain: gzip would give them a header and a
    non-zero size, and several scripts use `st_size > 0` to skip them.
    """
    found: set[Path] = set()
    for pattern in PATTERNS:
        found |= {p for p in ROOT.glob(pattern) if p.is_file()}
    if not include_newest:
        live = sorted(
            (p for p in ROOT.glob("logs/run_*.jsonl") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        if live:
            found.discard(live[-1])
    empty = sum(1 for p in found if p.stat().st_size == 0)
    return sorted(p for p in found if p.stat().st_size > 0), empty


def compress(path: Path, dry_run: bool) -> int:
    """Returns the bytes saved (0 if skipped)."""
    packed = path.with_suffix(path.suffix + ".gz")
    size = path.stat().st_size
    if size == 0:
        return 0
    if packed.exists():
        print(f"  skip (already packed): {path.relative_to(ROOT)}")
        return 0
    if dry_run:
        return size  # the real saving is ~95% of this; reported as an upper bound

    stat = path.stat()
    tmp = packed.with_suffix(".gz.partial")
    try:
        with open(path, "rb") as src, gzip.GzipFile(
            tmp, "wb", compresslevel=6, mtime=int(stat.st_mtime)
        ) as dst:
            shutil.copyfileobj(src, dst, CHUNK)

        with open(path, "rb") as src:
            want = _digest(src)
        with gzip.open(tmp, "rb") as src:
            got = _digest(src)
        if want != got:
            tmp.unlink(missing_ok=True)
            print(f"  FAILED verification, left alone: {path.relative_to(ROOT)}")
            return 0

        tmp.replace(packed)
        os.utime(packed, (stat.st_atime, stat.st_mtime))  # a script picks logs by mtime
        path.unlink()
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"  could not pack {path.relative_to(ROOT)}: {exc}")
        return 0
    return size - packed.stat().st_size


def decompress(path: Path, dry_run: bool) -> int:
    plain = Path(str(path)[: -len(".gz")])
    if plain.exists():
        print(f"  skip (already unpacked): {path.relative_to(ROOT)}")
        return 0
    if dry_run:
        return path.stat().st_size
    stat = path.stat()
    tmp = plain.with_suffix(plain.suffix + ".partial")
    try:
        with gzip.open(path, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, CHUNK)
        tmp.replace(plain)
        os.utime(plain, (stat.st_atime, stat.st_mtime))
        path.unlink()
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"  could not unpack {path.relative_to(ROOT)}: {exc}")
        return 0
    return plain.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--all", action="store_true", help="include the newest logs/run_*.jsonl")
    ap.add_argument("--decompress", action="store_true", help="restore every .gz log")
    args = ap.parse_args()

    if args.decompress:
        targets = sorted(p for p in ROOT.glob("**/*.jsonl.gz") if p.is_file())
        total = sum(decompress(p, args.dry_run) for p in targets)
        verb = "would restore" if args.dry_run else "restored"
        print(f"{verb} {len(targets)} logs, {total / 1048576:.1f} MB of plain text")
        return

    targets, empty = _targets(include_newest=args.all)
    tail = f" ({empty} empty logs left alone)" if empty else ""
    if not targets:
        print(f"nothing to pack{tail}")
        return
    before = sum(p.stat().st_size for p in targets)
    if args.dry_run:
        print(f"would pack {len(targets)} logs, {before / 1048576:.1f} MB "
              f"(expect ~95% back){tail}")
        for p in targets[:5]:
            print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1048576:.1f} MB")
        if len(targets) > 5:
            print(f"  ... and {len(targets) - 5} more")
        return

    saved = 0
    packed = 0
    for i, path in enumerate(targets, 1):
        gained = compress(path, dry_run=False)
        saved += gained
        packed += 1 if gained else 0
        if i % 10 == 0 or i == len(targets):
            print(f"  {i}/{len(targets)} done, {saved / 1048576:.0f} MB saved")
    print(f"packed {packed} logs: {before / 1048576:.1f} MB -> "
          f"{(before - saved) / 1048576:.1f} MB, {saved / 1048576:.1f} MB saved{tail}")


if __name__ == "__main__":
    main()
