"""What deck each run was building, measured at a fixed point.

Reporting the archetype of the *final* deck is biased: a run that lasts longer
collects more cards and is mechanically more likely to cross the commitment
threshold, so "committed decks go deeper" partly restates "longer runs are
longer". Measuring at the end of the **first elite fight** removes that: every
surviving run reaches one, at roughly the same stage, with roughly the same
number of picks behind it.

The deck is only visible during combat (draw + discard + hand + exhaust piles),
which is exactly why an in-combat checkpoint is used rather than a floor number.

Usage:
    python scripts/archetype_report.py                     # logs/
    python scripts/archetype_report.py stats/archive/set_20260828_20runs
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.strategy import cards as card_db  # noqa: E402

NO_ELITE = "(no elite reached)"
NONE = "(uncommitted)"


def _deck_from(record: dict) -> list[str] | None:
    combat = record.get("combat") or {}
    piles = ("draw_pile", "discard_pile", "hand", "exhaust_pile")
    names: list[str] = []
    for pile in piles:
        for card in (combat.get(pile) or []):
            name = card.get("name") if isinstance(card, dict) else card
            if name:
                names.append(name)
    if names:
        return names
    options = record.get("options") or {}
    hand = [c.get("name") for c in (options.get("hand") or []) if c.get("name")]
    return hand or None


def _split_runs(records: list[dict]) -> list[list[dict]]:
    runs: list[list[dict]] = []
    current: list[dict] = []
    last_floor = 0
    for rec in records:
        floor = (rec.get("state") or {}).get("floor") or 0
        if floor <= 1 and last_floor > 2 and current:
            runs.append(current)
            current = []
        last_floor = floor
        current.append(rec)
    if current:
        runs.append(current)
    return runs


def _archetype_at_first_elite(run: list[dict]) -> str:
    """Archetype of the deck as the first elite fight ends."""
    in_elite = False
    last_deck = None
    for rec in run:
        screen = (rec.get("state") or {}).get("state_type")
        if screen == "elite":
            in_elite = True
            deck = _deck_from(rec)
            if deck:
                last_deck = deck
        elif in_elite and screen not in ("elite", "hand_select", "card_select"):
            break  # first elite is over
    if last_deck is None:
        return NO_ELITE
    return card_db.dominant_archetype(last_deck) or NONE


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs")
    decision_logs = sorted(root.glob("run_*.jsonl"))
    summary_path = root / "runs.jsonl"
    if not decision_logs:
        print(f"No decision logs under {root}.")
        return

    summaries = []
    if summary_path.exists():
        summaries = [json.loads(l) for l in open(summary_path, encoding="utf-8") if l.strip()]

    runs: list[list[dict]] = []
    for path in decision_logs:
        records = []
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        runs += _split_runs(records)

    early = [_archetype_at_first_elite(r) for r in runs]

    print(f"=== {len(runs)} runs from {root} ===\n")
    print("Archetype fixed by the end of the first elite fight:")
    for name, n in Counter(early).most_common():
        print(f"   {name:20} {n:3d}")

    # Pair with outcomes where the summary file lines up run-for-run.
    if len(summaries) == len(runs):
        floors: dict[str, list[int]] = defaultdict(list)
        variants: dict[str, Counter] = defaultdict(Counter)
        for arch, s in zip(early, summaries):
            floors[arch].append(s.get("floor_reached", 0))
            variants[arch][s.get("act1_variant") or "?"] += 1
        print("\nDepth by that early archetype (unbiased by run length):")
        for arch, fl in sorted(floors.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            mix = ", ".join(f"{k}:{v}" for k, v in variants[arch].most_common())
            print(f"   {arch:20} n={len(fl):<3} avg floor {sum(fl)/len(fl):5.1f}  "
                  f"{sorted(fl)}   [{mix}]")
        print("\nFinal-deck archetype, for comparison (biased -- longer runs")
        print("collect more cards and cross the threshold more easily):")
        final: dict[str, list[int]] = defaultdict(list)
        for s in summaries:
            arch = card_db.dominant_archetype(s.get("deck") or []) or NONE
            final[arch].append(s.get("floor_reached", 0))
        for arch, fl in sorted(final.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            print(f"   {arch:20} n={len(fl):<3} avg floor {sum(fl)/len(fl):5.1f}")
    elif summaries:
        print(f"\n(Not pairing with outcomes: {len(runs)} reconstructed runs vs "
              f"{len(summaries)} summaries -- the run splitter and the recorder "
              f"disagree, so any pairing would be guesswork.)")


if __name__ == "__main__":
    main()
