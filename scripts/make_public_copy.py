"""Build the public copy of this project -- the code, none of the data.

    python scripts/make_public_copy.py --dry-run
    python scripts/make_public_copy.py [--dest DIR]

What comes out is a folder ready to push to GitHub: the bot, its tests, the
launchers and the documentation, with no logs, no archived run sets, no session
reports, and no `RECORD_RUNS` marker -- so somebody who clones it and runs the
bot gets a game played and not a folder full of files they never asked for.

Why generate it instead of forking it
-------------------------------------
Because a fork would drift, and the two copies would stop being the same bot.
The only difference between them is the *absence of a file*: `RECORD_RUNS` turns
recording on (see `bot/recording.py`), the development copy commits it, and this
script does not copy it. Every line of Python is identical, so a change made
here reaches the public copy by re-running this, and there is never a question
of which copy a bug lives in.

The file list comes from `git ls-files`, so anything untracked (caches, stray
logs, scratch files) cannot leak in by accident, and anything private has to be
excluded on purpose below.

Re-running it is safe: the destination is rebuilt from scratch, except for its
`.git` directory, so a published repo keeps its history and its remote.
"""
from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # sts2_bot/
REPO = ROOT.parent                                      # the git checkout
DEFAULT_DEST = Path(r"C:\sts2-silent-bot")
MARKER = ".public-export"

# Tracked files that must not be published. Paths are repo-relative, forward
# slashes, matched with fnmatch.
EXCLUDE = (
    ".claude/*",                       # local session settings
    "sts2_bot/logs/*",                 # recorded runs
    "sts2_bot/stats/*",                # archived sets, relic history, set history
    "sts2_bot/RECORD_RUNS",            # the switch itself -- this is the whole point
    "sts2_bot/HANDOFF.md",             # working notes for the next session
    "sts2_bot/CURRENT_WORK.md",
    "sts2_bot/TODO_TOMORROW.md",
    "sts2_bot/REPORT_*.md",            # per-session evaluation reports
    "sts2_bot/scripts/analysis/*",     # one-off diagnostics, hardcoded to this machine
)

# Publishing is hard to undo, so the result is checked for anything that
# identifies this machine rather than trusted not to contain any. Derived, not
# written out, so this file does not contain the strings it looks for -- the
# first version of it flagged itself.
def _forbidden() -> tuple[str, ...]:
    home = Path.home()
    return (home.name, str(home), home.as_posix())

# The one edit made on the way out: the README's first line points at the
# working notes, which are not published. Anchored, so a reworded README fails
# the export instead of silently shipping a broken link.
README_ANCHOR = """**Start with [HANDOFF.md](HANDOFF.md)** — the current state, how the project is
run and measured, where every log and report lives, and what is left to do."""
README_PUBLIC = """This is the public copy of the project: the same bot, without the recorded runs.
It writes nothing at all unless you ask it to -- see **[What it writes](#what-it-writes)**."""

GITIGNORE = """# The bot only writes when recording is turned on -- see bot/recording.py.
sts2_bot/logs/
sts2_bot/stats/
sts2_bot/RECORD_RUNS

__pycache__/
*.pyc
.pytest_cache/
""" + MARKER + "\n"

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".ps1", ".bat", ".cfg", ".ini", ".toml"}


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _excluded(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in EXCLUDE)


def _head() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown"


def _clear(dest: Path) -> None:
    """Empty the destination, keeping its git history and its export marker."""
    for child in dest.iterdir():
        if child.name in (".git", MARKER):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _transform(rel: str, data: bytes) -> bytes:
    """The README's opening pointer, swapped in the bytes.

    Byte-level rather than text-level so every other file can be copied
    verbatim: decoding and re-encoding would rewrite line endings, and the two
    copies are supposed to differ by exactly one missing file.
    """
    if rel != "sts2_bot/README.md":
        return data
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    anchor = README_ANCHOR.encode("utf-8").replace(b"\n", newline)
    replacement = README_PUBLIC.encode("utf-8").replace(b"\n", newline)
    if anchor not in data:
        sys.exit("README.md no longer contains the HANDOFF pointer this script "
                 "replaces -- update README_ANCHOR in make_public_copy.py")
    return data.replace(anchor, replacement, 1)


def _check_publishable(dest: Path) -> list[str]:
    problems = []
    for path in sorted(dest.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(dest).as_posix()
        if path.name == "RECORD_RUNS":
            problems.append(f"{rel}: the recording marker must not be published")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for word in _forbidden():
            if word in text:
                problems.append(f"{rel}: names this machine ({word})")
    return problems


def _recording_is_off(dest: Path) -> bool:
    """Ask the exported copy itself, rather than assuming."""
    probe = (
        "import sys; sys.path.insert(0, r'%s');"
        "from bot import recording;"
        "print(recording.enabled())" % (dest / "sts2_bot")
    )
    out = subprocess.run([sys.executable, "-c", probe],
                         capture_output=True, text=True, cwd=dest)
    return out.stdout.strip() == "False"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a destination this script did not create")
    args = ap.parse_args()
    dest: Path = args.dest

    tracked = _tracked()
    include = [p for p in tracked if not _excluded(p)]
    dropped = [p for p in tracked if _excluded(p)]

    if args.dry_run:
        print(f"{len(include)} files would be published, {len(dropped)} held back\n")
        print("held back:")
        for pattern in EXCLUDE:
            n = sum(1 for p in dropped if fnmatch.fnmatch(p, pattern))
            print(f"  {n:>4}  {pattern}")
        print(f"\ndestination: {dest}")
        return

    if dest.exists() and any(dest.iterdir()):
        if not (dest / MARKER).exists() and not args.force:
            sys.exit(f"{dest} is not empty and was not made by this script. "
                     f"Pass --force if you are sure.")
        _clear(dest)
    dest.mkdir(parents=True, exist_ok=True)

    for rel in include:
        src = REPO / rel
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if rel == "sts2_bot/README.md":
            out.write_bytes(_transform(rel, src.read_bytes()))
            shutil.copystat(src, out)
        else:
            shutil.copy2(src, out)  # byte for byte, line endings included

    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dest / MARKER).write_text(
        f"Generated by sts2_bot/scripts/make_public_copy.py\n"
        f"from {REPO} at commit {_head()} on {datetime.now():%Y-%m-%d %H:%M}.\n"
        f"\nDo not edit this copy: change the development copy and re-run the script.\n",
        encoding="utf-8",
    )

    problems = _check_publishable(dest)
    print(f"published {len(include)} files to {dest}")
    print(f"held back {len(dropped)} (logs, archived sets, reports, RECORD_RUNS)")
    if not _recording_is_off(dest):
        problems.append("the exported copy still reports recording as enabled")
    if problems:
        print("\nPROBLEMS -- do not upload this:")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    print("checked: no personal paths, no recording marker, recording reports off")
    print(f"\nnext: cd \"{dest}\" && git init && git add -A && git commit")


if __name__ == "__main__":
    main()
