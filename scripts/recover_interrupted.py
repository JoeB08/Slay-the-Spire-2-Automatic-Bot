"""Reconstruct runs that ended without reaching `game_over`.

    python scripts/recover_interrupted.py                       # the live logs
    python scripts/recover_interrupted.py --set set_20261113_20runs_set5
    python scripts/recover_interrupted.py --all --write

`RunRecorder` writes its summary line at `game_over` and nowhere else, so a run
the process never finishes exists only in the decision log and is invisible to
every analysis here -- all of which start from `runs.jsonl`.

That is not a hypothetical. Twice now it has dropped the most informative run
of a set:

  * Set 4 played a seventeenth run at 23:47 on 09-11. It is the one that got
    Soul Fysh to 4 HP at 14.8 damage per round, and the archive's conclusion
    that the boss losses "won none" was written without it. It reframed the
    output gap from 3x to 1.7x.
  * On 11-13 run 23 reached act 3, floor 39 -- through both bosses -- and then
    stalled on the Crystal Sphere screen for 3h35m. The deepest run of the
    night is in no summary.

What this writes, and where
---------------------------
`logs/runs_interrupted.jsonl`, **not** `runs.jsonl`. Appending to `runs.jsonl`
would silently change the numbers of sets whose reports are already written --
set 4 would quietly become 17 runs -- and a set's figures must not move under
whoever reads them next. So these rows sit beside it, marked
`"outcome": "interrupted"` and `"reconstructed": true`, and a tool that wants
them has to ask (`damage_per_round.py --with-interrupted`).

The record is deliberately partial. Floor, act, the HP track, the timings, the
decision count and the final fight all survive in the log; `killed_by` and the
deck do not, because the run did not end and the payload carries no card
piles outside combat. Damage taken is summed from observed HP drops, the same
way `RunRecorder` does it, so it is a floor rather than a total -- anything
that happened between two polls is not in it.

Fragments
---------
A restart makes a new decision log, so one interrupted run can be spread over
many. The 11-13 stall left 53 of them, every one resuming the same save at
act 3 floor 39. Consecutive unrecorded stretches are merged when the later one
starts at or above the earlier one's deepest floor -- a continuation of the
same run rather than a new one, which always restarts at floor 1. The merged
row records `fragments` and sums only the time actually spent playing, not the
wall-clock span across the relaunches.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import log_io  # noqa: E402

LOGS = ROOT / "logs"
ARCHIVE = ROOT / "stats" / "archive"
OUT_NAME = "runs_interrupted.jsonl"
MIN_DECISIONS = 10          # below this it is a menu visit, not a run
MIN_FLOOR = 2               # a run that never left floor 1 is not worth a row
# A relaunch takes about two minutes, so fragments of one run are minutes
# apart. Without this bound the merge fused five unrelated fragments spread
# across set 4's whole session into one nonsensical row, because they all
# happened to be deeper than the one before.
MERGE_GAP_SECONDS = 900


def _floor(row: dict[str, Any]) -> int:
    state = row.get("state") or {}
    if isinstance(state.get("floor"), int):
        return state["floor"]
    run = (row.get("raw") or {}).get("run") or {}
    return run.get("floor") or 0


def _act(row: dict[str, Any]) -> int:
    state = row.get("state") or {}
    if isinstance(state.get("act"), int):
        return state["act"]
    run = (row.get("raw") or {}).get("run") or {}
    return run.get("act") or 0


def _hp(row: dict[str, Any]) -> Optional[int]:
    state = row.get("state") or {}
    if isinstance(state.get("hp"), int) and state.get("hp") > 0:
        return state["hp"]
    player = (row.get("raw") or {}).get("player") or {}
    hp = player.get("hp")
    return hp if isinstance(hp, int) else None


def split_runs(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cut a session log into runs.

    Two things end a run here: the floor going backwards to 0 or 1 (a new run
    always starts there), and a long silence. The silence matters because the
    rows handed to this function have had every recorded run's rows removed, so
    what is left can jump hours in one step -- without the gap cut, two
    unrelated leftovers an hour apart became a single run.
    """
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    deepest = 0
    previous_ts = None
    for row in rows:
        floor = _floor(row)
        ts = row.get("ts") if isinstance(row.get("ts"), (int, float)) else None
        quiet = (previous_ts is not None and ts is not None
                 and ts - previous_ts > MERGE_GAP_SECONDS)
        if current and ((floor <= 1 and deepest > 1) or quiet):
            groups.append(current)
            current, deepest = [], 0
        current.append(row)
        deepest = max(deepest, floor)
        if ts is not None:
            previous_ts = ts
    if current:
        groups.append(current)
    return groups


def known_windows(runs_file: Path) -> list[tuple[float, float]]:
    out = []
    if not runs_file.exists():
        return out
    for line in runs_file.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            run = json.loads(line)
        except ValueError:
            continue
        start, end = run.get("started_ts"), run.get("ended_ts")
        if start and end:
            out.append((start - 2, end + 5))
    return out


def all_windows() -> list[tuple[float, float]]:
    """Every run the project has recorded, from every runs.jsonl it keeps.

    Asked per directory instead, this flags runs that *were* recorded but whose
    summary row was archived somewhere else -- which is normal: the two pre-fix
    runs of 11-13 sit in their own directory while their decision log stayed
    with the set it was written during, and a resumed run gets a directory of
    its own. The question is whether a run was recorded anywhere, not whether
    it was recorded here.
    """
    windows: list[tuple[float, float]] = []
    for runs_file in [LOGS / "runs.jsonl"] + sorted(ARCHIVE.glob("*/runs.jsonl")):
        windows.extend(known_windows(runs_file))
    return windows


def summarise(group: list[dict[str, Any]], source_logs: list[str]) -> dict[str, Any]:
    floors = [_floor(r) for r in group]
    acts = [_act(r) for r in group]
    hps = [h for h in (_hp(r) for r in group) if h is not None]
    stamps = [r["ts"] for r in group if isinstance(r.get("ts"), (int, float))]

    damage = 0
    previous = None
    for hp in hps:
        if previous is not None and hp < previous:
            damage += previous - hp
        previous = hp

    return {
        "run_id": f"interrupted_{int(stamps[0])}" if stamps else "interrupted",
        "outcome": "interrupted",
        "reconstructed": True,
        "reconstructed_note": "rebuilt from the decision log; the run never reached game_over",
        "floor_reached": max(floors) if floors else 0,
        "act_reached": max(acts) if acts else 0,
        "damage_taken": damage,
        "damage_taken_is_a_floor": True,
        "hp_low_water": min(hps) if hps else None,
        "final_hp": hps[-1] if hps else None,
        "max_hp": max((((r.get("raw") or {}).get("player") or {}).get("max_hp") or 0)
                      for r in group) or None,
        "decisions": len(group),
        "killed_by": None,
        "deck": [],
        "deck_size": None,
        "started_ts": min(stamps) if stamps else None,
        "ended_ts": max(stamps) if stamps else None,
        "duration_s": (max(stamps) - min(stamps)) if len(stamps) > 1 else 0,
        "decision_log": source_logs[0] if source_logs else None,
        "decision_logs": source_logs,
        "fragments": len(source_logs),
    }


def _shallowest_played(group: list[dict[str, Any]]) -> int:
    """The lowest floor this stretch actually played on, menu rows aside.

    This is what tells a continuation from a new run: a resumed save picks
    up at the floor it stalled on, while a new run always starts at floor 1.
    Comparing the *first* row instead does not work -- every restart begins
    at the main menu, which reports floor 0, so nothing ever matched and the
    11-13 stall came back as 53 separate runs.
    """
    played = [_floor(r) for r in group if _floor(r) > 0]
    return min(played) if played else 0


def merge(records: list[dict[str, Any]], groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Join fragments of one run that a restart split across logs."""
    merged: list[dict[str, Any]] = []
    held_groups: list[list[dict[str, Any]]] = []
    for record, group in zip(records, groups):
        gap = (record["started_ts"] or 0) - (merged[-1]["ended_ts"] or 0) if merged else None
        # Merge a *stall* and nothing else: consecutive stretches minutes
        # apart that never left one floor. That is the shape a relaunch loop
        # leaves -- 60 stretches all sitting on act 3 floor 39 -- and it is
        # unambiguous. Looser rules kept chaining unrelated leftovers into
        # 90-minute "runs" that never happened. A run that genuinely carried
        # on past a restart comes back as two rows instead of one, which is
        # visible and honest rather than invented.
        same_floor = bool(merged) and (record["floor_reached"] == merged[-1]["floor_reached"]
                      and _shallowest_played(group) == record["floor_reached"])
        if merged and gap is not None and 0 <= gap <= MERGE_GAP_SECONDS \
                and record["act_reached"] == merged[-1]["act_reached"] \
                and same_floor:
            previous = merged.pop()
            held = held_groups.pop()
            combined = held + group
            logs = previous["decision_logs"] + record["decision_logs"]
            # Wall clock across a relaunch is not play; sum the fragments.
            played = previous["duration_s"] + record["duration_s"]
            fresh = summarise(combined, logs)
            fresh["duration_s"] = played
            fresh["wall_clock_s"] = (record["ended_ts"] or 0) - (previous["started_ts"] or 0)
            merged.append(fresh)
            held_groups.append(combined)
            continue
        merged.append(record)
        held_groups.append(group)
    return merged


def scan(directory: Path, runs_file: Path) -> list[dict[str, Any]]:
    windows = all_windows()
    logs = sorted(log_io.log_paths(directory, "run_*.jsonl"), key=log_io.sort_key)

    def recorded(row: dict[str, Any]) -> bool:
        ts = row.get("ts")
        if not isinstance(ts, (int, float)):
            return False
        return any(start <= ts <= end for start, end in windows)

    records: list[dict[str, Any]] = []
    groups: list[list[dict[str, Any]]] = []
    for path in logs:
        # Row by row, because one stretch of a log can hold the tail of a run
        # that was recorded and the whole of one that was not. Testing whole
        # stretches for overlap hid set 4's seventeenth run: it shared a
        # minute with the run before it, so the stretch counted as recorded
        # and the fight that reframed the output gap stayed invisible.
        rows = [r for r in log_io.rows(path) if not recorded(r)]
        if not rows:
            continue
        for group in split_runs(rows):
            stamps = [r["ts"] for r in group if isinstance(r.get("ts"), (int, float))]
            if not stamps or len(group) < MIN_DECISIONS:
                continue
            if max(_floor(r) for r in group) < MIN_FLOOR:
                continue
            records.append(summarise(group, [log_io.display_name(path)]))
            groups.append(group)
    return merge(records, groups)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", help="an archive directory name, or a path")
    ap.add_argument("--all", action="store_true", help="every archived set, and the live logs")
    ap.add_argument("--write", action="store_true",
                    help=f"write {OUT_NAME} into each scanned directory")
    args = ap.parse_args()

    if args.all:
        targets = [d for d in sorted(ARCHIVE.iterdir())
                   if d.is_dir() and log_io.log_paths(d, "run_*.jsonl")] + [LOGS]
    elif args.set:
        directory = Path(args.set)
        if not directory.is_dir():
            directory = ARCHIVE / args.set
        targets = [directory]
    else:
        targets = [LOGS]

    total = 0
    for directory in targets:
        found = scan(directory, directory / "runs.jsonl")
        if not found:
            continue
        total += len(found)
        print(f"\n{directory.name}: {len(found)} interrupted run(s)")
        for record in found:
            note = f", {record['fragments']} fragments" if record["fragments"] > 1 else ""
            print(f"  act {record['act_reached']} floor {record['floor_reached']}, "
                  f"{record['decisions']} decisions, {record['damage_taken']} damage taken, "
                  f"hp low {record['hp_low_water']}, {record['duration_s'] / 60:.0f} min{note}")
        if args.write:
            out = directory / OUT_NAME
            out.write_text("\n".join(json.dumps(r) for r in found) + "\n", encoding="utf-8")
            print(f"  wrote {out}")
    if not total:
        print("no interrupted runs found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
