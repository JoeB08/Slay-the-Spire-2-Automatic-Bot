"""Check every action the bot can send against the ones the mod actually has.

The Crystal Sphere stall of 2026-11-13 was four action names that do not exist
in STS2_MCP. The bot sent them, the mod answered "Unknown action", and because
the rejection detail was being dropped the logs said only `-> None`. It cost
3h35m, 52 game relaunches and a run that had reached act 3 floor 39 -- to learn
something that was sitting in the mod's own file the whole time.

The mod is a single managed assembly, and .NET keeps string literals as UTF-16
in the `#US` heap, so its entire vocabulary -- every action name, field name
and help string -- can be read straight off disk. This turns "does the mod have
this action?" from a question answered by losing a run into one answered in a
second.

    python scripts/verify_mod_actions.py            # audit, exit 1 on a miss
    python scripts/verify_mod_actions.py --help-text # the mod's own docs
    python scripts/verify_mod_actions.py --grep crystal

**Absence is conclusive; presence is not.** .NET metadata carries method names
as well as string literals, so a name can appear in the pool because the mod
has a *method* of that name rather than an action. `claim_treasure_relic` and
`treasure_claim_relic` are both in there, and the logs settle which one is
real: the former succeeds 15 times. So treat a miss as a defect to fix and a
hit as "not the Crystal Sphere failure", nothing stronger -- confirm a name
you care about against `--help-text` or a log.

Nothing here is imported by `bot/`, and nothing is written. It only reads.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The default install. `--dll` overrides it.
DEFAULT_DLL = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2\mods\STS2_MCP.dll"
)
BOT_DIR = Path(__file__).resolve().parent.parent / "bot"

# How the bot names an action in code: `return "name", {...}` from a decide
# function, or a module constant holding one.
ACTION_PATTERNS = (
    re.compile(r'return\s+"([a-z][a-z0-9_]{2,40})"\s*,'),
    re.compile(r'ACTION[A-Z_]*\s*=\s*"([a-z][a-z0-9_]{2,40})"'),
)
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{2,45}")

# Actions that are the bot's own vocabulary rather than the mod's -- they never
# go over the wire as an action name.
NOT_SENT_TO_THE_MOD = {"state"}


def mod_strings(dll: Path) -> str:
    """Every UTF-16 string literal in the assembly, both byte alignments.

    The `#US` heap does not begin on an even offset, so decoding only from
    byte 0 silently misses most of it -- an audit written that way reported
    `play_card` as missing, which the bot demonstrably sends hundreds of times
    a run. Both alignments are decoded and concatenated.
    """
    raw = dll.read_bytes()
    return (raw.decode("utf-16-le", errors="ignore") + "\n"
            + raw[1:].decode("utf-16-le", errors="ignore"))


def bot_actions(bot_dir: Path) -> set[str]:
    found: set[str] = set()
    for path in sorted(bot_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in ACTION_PATTERNS:
            found |= set(pattern.findall(text))
    return found - NOT_SENT_TO_THE_MOD


def documented_calls(pool: str) -> list[str]:
    """The mod's own per-screen help: any readable line with a backticked call."""
    out: list[str] = []
    for block in re.finditer(r"[ -~\n]{25,}", pool):
        for line in re.findall(r"[^\n]{20,}", block.group()):
            if re.search(r"`[a-z_]+\(", line) and line.strip() not in out:
                out.append(line.strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dll", type=Path, default=DEFAULT_DLL)
    ap.add_argument("--help-text", action="store_true",
                    help="print the mod's own documented calls")
    ap.add_argument("--grep", metavar="TEXT",
                    help="print every identifier in the mod containing TEXT")
    args = ap.parse_args()

    if not args.dll.exists():
        print("mod not found: %s" % args.dll)
        print("pass --dll if the game is installed in another Steam library.")
        return 2

    pool = mod_strings(args.dll)

    if args.grep:
        hits = sorted(i for i in set(IDENTIFIER.findall(pool)) if args.grep in i)
        print("%d identifiers containing %r:" % (len(hits), args.grep))
        for hit in hits:
            print("  " + hit)
        return 0

    if args.help_text:
        for line in documented_calls(pool):
            print("  " + line)
        return 0

    known = set(IDENTIFIER.findall(pool))
    actions = bot_actions(BOT_DIR)
    missing = sorted(a for a in actions if a not in known)

    print("%d actions in bot/, checked against %s" % (len(actions), args.dll.name))
    if not missing:
        print("no miss -- nothing the bot sends is outright absent from the mod")
        print("(a hit can be a method name; see the note in this file's docstring)")
        return 0
    print("\n%d NOT in the mod -- each would come back 'Unknown action':" % len(missing))
    for name in missing:
        print("  !! " + name)
    print("\nThis is the Crystal Sphere failure. Find the real name with:")
    print("  python scripts/verify_mod_actions.py --grep <screen>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
