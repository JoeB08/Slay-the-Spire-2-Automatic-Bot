"""Report relic <-> run-outcome correlations.

Reads `stats/relic_history.jsonl`, which lives outside `logs/` and is never
wiped between evaluation sets -- relic signal only emerges across many runs,
so the sample has to accumulate.

Usage:
    python scripts/relic_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import relic_stats  # noqa: E402


def main() -> None:
    history = relic_stats.load_history()
    if not history:
        print(f"No runs recorded yet ({relic_stats.HISTORY_FILE}).")
        return

    floors = [h.get("floor_reached", 0) for h in history]
    reached_act2 = sum(1 for h in history if h.get("act_reached", 1) >= 2)
    print(f"=== {len(history)} runs recorded ===")
    print(f"avg floor {sum(floors)/len(floors):.1f} | best {max(floors)} | reached act 2: {reached_act2}")
    print()

    print("First relic acquired (the Neow choice -- the one relic decision made")
    print("at a fixed point in every run, so it is comparable across runs):")
    for row in relic_stats.starting_relic_summary():
        print(f"   {row['relic']:22} n={row['runs']:<3} avg floor {row['avg_floor']:5.1f}   {row['floors']}")
    print()

    by_act = relic_stats.by_act_summary()
    if by_act:
        print("Relics by the act they were acquired in -- each act opens with its")
        print("own choice from a different pool, so they are kept separate:")
        for act, rows in by_act.items():
            print(f"\n  --- ACT {act} ---")
            for row in rows:
                marker = "" if row["runs"] > 1 else "  (single run)"
                print(f"     {row['relic']:22} n={row['runs']:<3} avg floor {row['avg_floor']:5.1f}   "
                      f"{row['floors']}{marker}")
        print()
    else:
        print("(no per-act relic data yet -- runs recorded before act tracking")
        print(" existed are skipped rather than guessed at)")
        print()

    print("All relics held at end of run (biased: a relic picked up on floor 30")
    print("looks good merely for having been acquired late -- weigh by n):")
    for row in relic_stats.summarise():
        if row["runs"] < 2:
            continue
        print(f"   {row['relic']:22} n={row['runs']:<3} avg floor {row['avg_floor']:5.1f} "
              f"(best {row['best']}, worst {row['worst']})")

    singles = [r for r in relic_stats.summarise() if r["runs"] == 1]
    if singles:
        print(f"\n   ({len(singles)} relics seen in only one run each -- no signal yet)")


if __name__ == "__main__":
    main()
