"""Persistent relic <-> run-outcome history.

Deliberately stored **outside `logs/`** so it survives log wipes: the decision
logs are routinely cleared between evaluation sets, but relic correlations
only become meaningful as runs accumulate over many sets. Wiping them would
reset the sample every time.

One JSON line per finished run, appended forever. Small enough to keep
indefinitely (a few hundred bytes per run).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

STATS_DIR = Path(__file__).parent.parent / "stats"
HISTORY_FILE = STATS_DIR / "relic_history.jsonl"

# The Silent always starts with this, so it carries no signal.
STARTING_RELIC = "Ring of the Snake"


def record_run(
    relics: list[str],
    floor_reached: int,
    act_reached: int,
    outcome: str,
    run_id: Optional[str] = None,
    damage_taken: Optional[int] = None,
    relic_act: Optional[dict[str, int]] = None,
    ascension: int = 0,
    act1_variant: Optional[str] = None,
    explore: bool = False,
) -> None:
    """Append one run's relics and how far it got.

    `relic_act` maps each relic to the act it was acquired in. Each act starts
    with its own relic choice from a different pool (Neow in act 1, Tezcatara
    in act 2, ...), so those are distinct decisions and must be analysed
    separately -- pooling them compares options that were never on the same
    screen.
    """
    try:
        STATS_DIR.mkdir(exist_ok=True)
        entry = {
            "run_id": run_id,
            "relics": list(relics or []),
            "relic_act": dict(relic_act or {}),
            "floor_reached": floor_reached,
            "act_reached": act_reached,
            "outcome": outcome,
            "damage_taken": damage_taken,
            "ascension": ascension,
            "explore": explore,
            # Which Act 1 this run rolled. Kept in the same file rather than
            # split out, so the sample keeps growing -- filter at read time.
            "act1_variant": act1_variant,
        }
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # stats are an optimisation, never worth failing a run over


# Every comparison in this project assumes Ascension 0. The game defaults the
# character-select screen to the highest tier unlocked, and the mod's API
# exposes no ascension control at all -- not even to read before embarking --
# so a run at a different tier can start without the bot being able to prevent
# it. Those runs are real, and kept, but must never be averaged in with the
# baseline: harder enemies would look like the bot getting worse.
BASELINE_ASCENSION = 0


def load_history(ascension: Optional[int] = BASELINE_ASCENSION) -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    out = []
    with open(HISTORY_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Rows written before ascension was recorded predate the first
            # non-zero run, so absence means 0 rather than "unknown".
            if ascension is not None and entry.get("ascension", 0) != ascension:
                continue
            out.append(entry)
    return out


def summarise() -> list[dict[str, Any]]:
    """Per-relic average floor reached, most successful first.

    Every relic *held at the end of a run* is credited, so a relic picked up
    on floor 30 will look good simply for having been acquired late. Treat
    `runs` as the confidence measure and prefer relics seen across many runs.
    """
    floors: dict[str, list[int]] = defaultdict(list)
    for entry in load_history():
        for relic in entry.get("relics", []):
            if relic == STARTING_RELIC:
                continue
            floors[relic].append(entry.get("floor_reached", 0))

    rows = [
        {
            "relic": relic,
            "runs": len(values),
            "avg_floor": sum(values) / len(values),
            "best": max(values),
            "worst": min(values),
        }
        for relic, values in floors.items()
    ]
    rows.sort(key=lambda r: (-r["avg_floor"], -r["runs"]))
    return rows


def starting_relic_summary() -> list[dict[str, Any]]:
    """Same, but only for the *first* relic acquired after the starter.

    This is the Neow choice, which is the one relic decision the bot actually
    makes at a fixed point in every run -- so it's comparable across runs in a
    way that later pickups are not.
    """
    floors: dict[str, list[int]] = defaultdict(list)
    for entry in load_history():
        relics = [r for r in entry.get("relics", []) if r != STARTING_RELIC]
        if not relics:
            continue
        floors[relics[0]].append(entry.get("floor_reached", 0))

    rows = [
        {
            "relic": relic,
            "runs": len(values),
            "avg_floor": sum(values) / len(values),
            "floors": sorted(values),
        }
        for relic, values in floors.items()
    ]
    rows.sort(key=lambda r: -r["avg_floor"])
    return rows


def by_act_summary() -> dict[int, list[dict[str, Any]]]:
    """Relic performance grouped by the act it was acquired in.

    Each act opens with its own relic choice from a different pool (Neow in
    act 1, Tezcatara in act 2, ...). Pooling them would compare options that
    were never on the same screen, so the acts are kept apart.

    Runs recorded before per-act tracking existed have no `relic_act` map and
    are skipped rather than guessed at.
    """
    per_act: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for entry in load_history():
        acts = entry.get("relic_act") or {}
        if not acts:
            continue
        floor = entry.get("floor_reached", 0)
        for relic, act in acts.items():
            if relic == STARTING_RELIC:
                continue
            per_act[int(act)][relic].append(floor)

    out: dict[int, list[dict[str, Any]]] = {}
    for act, relics in sorted(per_act.items()):
        rows = [
            {
                "relic": relic,
                "runs": len(values),
                "avg_floor": sum(values) / len(values),
                "floors": sorted(values),
            }
            for relic, values in relics.items()
        ]
        rows.sort(key=lambda r: (-r["avg_floor"], -r["runs"]))
        out[act] = rows
    return out
