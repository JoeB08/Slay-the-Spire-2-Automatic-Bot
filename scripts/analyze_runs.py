"""Summarise completed runs to find what to improve next.

Reads logs/runs.jsonl (one line per finished run) and, where useful, the
per-decision logs those runs point at. Prints an aggregate picture plus the
specific signals that tend to indicate a fixable problem rather than variance.

Usage:
    python scripts/analyze_runs.py [--last N]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
RUNS_FILE = LOG_DIR / "runs.jsonl"


def load_runs(last: int | None) -> list[dict]:
    if not RUNS_FILE.exists():
        return []
    runs = []
    with open(RUNS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return runs[-last:] if last else runs


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _bar(value: float, scale: float, width: int = 28) -> str:
    filled = int(round((value / scale) * width)) if scale else 0
    return "#" * max(0, min(width, filled))


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise bot runs")
    parser.add_argument("--last", type=int, default=None, help="only the last N runs")
    args = parser.parse_args()

    runs = load_runs(args.last)
    if not runs:
        print(f"No finished runs recorded yet ({RUNS_FILE}).")
        print("Runs are written when a run ends; play some with START BOT first.")
        return

    print(f"=== {len(runs)} run(s) ===\n")

    floors = [r.get("floor_reached", 0) for r in runs]
    acts = [r.get("act_reached", 0) for r in runs]
    print(f"Floor reached : avg {_mean(floors):.1f}  best {max(floors)}  worst {min(floors)}")
    print(f"Act reached   : avg {_mean(acts):.1f}  best {max(acts)}")
    print(f"Damage taken  : avg {_mean([r.get('damage_taken', 0) for r in runs]):.0f}")
    print(f"Gold peak     : avg {_mean([r.get('max_gold', 0) for r in runs]):.0f}")
    print(f"Deck size end : avg {_mean([r.get('deck_size', 0) for r in runs]):.1f}")
    print()

    print("Per-run:")
    worst = max(floors) or 1
    for r in runs:
        killer = ""
        if r.get("killed_by") and r["killed_by"].get("enemies"):
            killer = ", ".join(e.get("name") or "?" for e in r["killed_by"]["enemies"][:3])
        print(
            f"  act{r.get('act_reached', 0)} f{r.get('floor_reached', 0):>3} "
            f"{_bar(r.get('floor_reached', 0), worst):<28} "
            f"{r.get('outcome', '?'):<6} {killer}"
        )
    print()

    # What kills the bot -- the clearest signal of where to spend effort.
    killers: Counter = Counter()
    killer_floor: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        kb = r.get("killed_by") or {}
        for e in kb.get("enemies", []):
            name = e.get("name") or "?"
            killers[name] += 1
            killer_floor[name].append(r.get("floor_reached", 0))
    if killers:
        print("Died to (enemy present in the final fight):")
        for name, count in killers.most_common(10):
            print(f"  {count:>2}x  {name}  (avg floor {_mean(killer_floor[name]):.0f})")
        print()

    # Screens where the bot burned actions without progressing.
    recoveries = sum(r.get("recovery_events", 0) for r in runs)
    if recoveries:
        print(f"Recovery events (stuck/cycle/stale): {recoveries} across {len(runs)} runs")
        event_totals: Counter = Counter()
        for r in runs:
            for key, value in (r.get("counts") or {}).items():
                if key.startswith("event:"):
                    event_totals[key[len("event:"):]] += value
        for name, count in event_totals.most_common():
            print(f"  {count:>3}  {name}")
        print()

    # Where decisions actually go -- disproportionate screens are suspicious.
    screen_totals: Counter = Counter()
    action_totals: Counter = Counter()
    for r in runs:
        for key, value in (r.get("counts") or {}).items():
            if key.startswith("screen:"):
                screen_totals[key[len("screen:"):]] += value
            elif key.startswith("action:"):
                action_totals[key[len("action:"):]] += value
    total_decisions = sum(screen_totals.values()) or 1
    print("Decisions by screen:")
    for name, count in screen_totals.most_common(12):
        print(f"  {count:>6}  {100 * count / total_decisions:>5.1f}%  {name}")
    print()

    # Cards the bot ends runs with, to sanity-check deck building.
    card_counts: Counter = Counter()
    for r in runs:
        for name in r.get("deck", []):
            card_counts[name] += 1
    print("Most common cards in final decks:")
    for name, count in card_counts.most_common(15):
        print(f"  {count:>3}  {name}")
    print()

    starters = sum(
        1 for r in runs for n in r.get("deck", []) if n in ("Strike", "Defend")
    )
    print(f"Starter cards still in final decks: {starters} total "
          f"({starters / len(runs):.1f} per run) -- high means removals aren't keeping up.")


if __name__ == "__main__":
    main()
