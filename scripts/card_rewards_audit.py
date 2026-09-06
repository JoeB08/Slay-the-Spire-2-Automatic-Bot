"""Count card rewards the bot walked away from without opening.

    python scripts/card_rewards_audit.py [logs/run_YYYYMMDD_HHMMSS.jsonl]

Defaults to the most recently modified per-process decision log.

Why this exists
---------------
`rewards.decide_rewards` remembers card rewards it opened and deliberately
skipped, so that re-claiming them does not ping-pong forever. When that memory
leaked across runs, `_claimable()` filtered the card off the rewards screen and
the bot issued `proceed` -- walking past the card without ever opening it.

On the 20-run set of 2026-08-29 this cost **42 abandoned cards against 42
taken**: roughly half of every card offer in the game, ~2.5 per run. Nine
genuine skips poisoned those floors for every later run, and 42 of 42
abandonments landed on a previously-skipped floor. Because the poisoned floors
are low ones every run passes through, the handicap was uniform -- which is why
deck size showed no correlation with run depth on that set.

A `proceed` is only counted as an abandonment when NO `card_reward` screen
appeared earlier in the same rewards block; leaving a screen that still lists a
card after picking from it is correct cleanup, not a defect.

After the run-scoped fix, `abandoned` should be at or near zero. If it is not,
the fix did not take.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _latest_log() -> Path | None:
    logs = sorted(ROOT.glob("logs/run_*.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def _stype(row: dict) -> str | None:
    return (row.get("state") or {}).get("state_type")


def audit(path: Path) -> dict:
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    taken = sum(1 for r in rows if r.get("action") == "select_card_reward")
    skips, abandoned, cleanup = [], [], 0

    for i, r in enumerate(rows):
        st = r.get("state") or {}
        if r.get("action") == "skip_card_reward":
            skips.append((st.get("act"), st.get("floor")))
            continue
        if _stype(r) != "rewards" or r.get("action") != "proceed":
            continue
        items = (r.get("options") or {}).get("items") or []
        if not any((it.get("type") or "").lower() == "card" for it in items):
            continue
        # Walk back through this contiguous rewards block looking for a
        # card_reward screen. If one appeared, the card was genuinely offered.
        j, opened = i - 1, False
        while j >= 0 and _stype(rows[j]) in ("rewards", "card_reward", "card_select"):
            if _stype(rows[j]) == "card_reward":
                opened = True
            j -= 1
        if opened:
            cleanup += 1
        else:
            abandoned.append((st.get("act"), st.get("floor")))

    seeded = sum(1 for a in abandoned if a in set(skips))
    return {
        "rows": len(rows),
        "taken": taken,
        "skipped": len(skips),
        "abandoned": len(abandoned),
        "cleanup": cleanup,
        "seeded": seeded,
        "skip_floors": collections.Counter(skips),
        "abandon_floors": collections.Counter(abandoned),
    }


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_log()
    if not path or not path.exists():
        print("No decision log found under logs/.")
        return
    a = audit(path)
    offered = a["taken"] + a["skipped"] + a["abandoned"]
    print(f"{path.name} - {a['rows']} decisions\n")
    print(f"  cards taken               {a['taken']}")
    print(f"  cards skipped on purpose  {a['skipped']}")
    print(f"  cards ABANDONED unopened  {a['abandoned']}"
          + (f"   ({a['abandoned'] / offered:.0%} of all offers)" if offered else ""))
    print(f"  correct post-pick cleanup {a['cleanup']}")
    if a["abandoned"]:
        print(f"\n  of those, at a floor a real skip was recorded: "
              f"{a['seeded']}/{a['abandoned']}"
              + ("   <-- consistent with the skip-memory leak"
                 if a["seeded"] == a["abandoned"] else ""))
        print(f"  skipped floors:   {dict(a['skip_floors'])}")
        print(f"  abandoned floors: {dict(a['abandon_floors'].most_common())}")
    else:
        print("\n  No abandoned cards. The run-scoped skip memory is holding.")


if __name__ == "__main__":
    main()
