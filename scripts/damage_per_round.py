"""Damage dealt per round in the fight that ended the run.

    python scripts/damage_per_round.py                  # the live set
    python scripts/damage_per_round.py --set set_20261113_20runs_set5
    python scripts/damage_per_round.py --all            # one row per archived set
    python scripts/damage_per_round.py --per-run        # every run, not just the groups

Why this exists
---------------
Floor depth can no longer resolve a change. Five consecutive sets -- 08-30
batch 2, 08-30 act 2 work, set 2, set 3, set 4, set 5 -- land between 18.8 and
24.1 with 95% margins of +/-3.5 to +/-5.0, so no pair of them is
distinguishable. Over a hundred runs of play have not shown a single change to
be an improvement, and no further set will, because depth is dominated by
*which* boss a run draws and how act 2 goes rather than by the change under
test.

Damage per round in the deciding fight separates the same runs completely. On
set 4's 16 runs, recomputed from the raw payloads:

    reached act 2      n=7  mean 27.8  range 22.3 - 50.9
    lost the act 1 boss n=7  mean  9.6  range  7.5 - 13.0

The weakest run that reached act 2 is above the strongest run that lost the
boss -- the groups do not overlap at all. It also separates *within* one fight
type, which rules out "different bosses have different HP": the six Soul Fysh
fights split 8.6 (five losses) against 14.8 (the one that nearly won). So a
deck change worth 40% of output should show on about 8 runs, where floor depth
needs far more than 20.

It is a leading indicator, not the goal. A change that raises output but gets
the bot killed sooner would look good here, which is why damage taken per
floor stays in the report beside it.

How the number is built
-----------------------
Each run row in `runs.jsonl` names the decision log it came from, so there is
no guessing about which log belongs to which run. Within that log, the last
run of consecutive combat payloads on one floor is the deciding fight. For
each enemy that appeared in it, damage dealt is the largest `max_hp - hp` ever
observed for that `entity_id`; an enemy that vanishes from the list while the
fight continues died, so it counts its whole `max_hp` (otherwise every killed
minion would be undercounted by its last-seen HP). Rounds is the highest
`round` the payload reported. Summons are counted like anything else, which is
the one place this disagrees with the set 4 archive's story-based figures.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import log_io  # noqa: E402
from bot.strategy import cards  # noqa: E402

LOGS = ROOT / "logs"
ARCHIVE = ROOT / "stats" / "archive"


# --- the fight ---------------------------------------------------------------

def _battle(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    return (row.get("raw") or {}).get("battle")


def _floor(row: dict[str, Any]) -> Optional[int]:
    state = row.get("state") or {}
    if "floor" in state:
        return state.get("floor")
    return ((row.get("raw") or {}).get("run") or {}).get("floor")


def _where(row: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    """Act and floor together: floor 17 exists in every act."""
    state = row.get("state") or {}
    run = (row.get("raw") or {}).get("run") or {}
    act = state.get("act") if isinstance(state.get("act"), int) else run.get("act")
    return act, _floor(row)


def final_fight(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last fight in the log, whole.

    Built from the rows that carry a battle payload, on the act and floor of
    the last of them, walking back while the round counter does not increase.
    Earlier versions cut the fight wherever a row had no battle payload, which
    is what every in-combat selection screen looks like -- so a long fight was
    measured from its last interruption onwards and read as a fraction of its
    real output.
    """
    battles = [r for r in rows if _battle(r) is not None]
    if not battles:
        return []
    here = _where(battles[-1])
    same_place = [r for r in battles if _where(r) == here]

    span = [same_place[-1]]
    for row in reversed(same_place[:-1]):
        this_round = (_battle(row) or {}).get("round") or 0
        next_round = (_battle(span[0]) or {}).get("round") or 0
        if this_round <= next_round:
            span.insert(0, row)
        else:
            break                      # the round counter restarted: another fight
    return span


def fight_damage(span: list[dict[str, Any]]) -> dict[str, Any]:
    """Damage dealt, rounds, and who was in the fight."""
    best: dict[str, int] = {}          # entity_id -> most damage ever seen on it
    full_hp: dict[str, int] = {}       # entity_id -> max_hp
    names: dict[str, str] = {}
    present_now: set[str] = set()
    gone: set[str] = set()
    rounds = 0

    for row in span:
        battle = _battle(row) or {}
        rounds = max(rounds, battle.get("round") or 0)
        seen = set()
        for enemy in battle.get("enemies") or []:
            eid = enemy.get("entity_id") or enemy.get("name") or "?"
            seen.add(eid)
            names[eid] = enemy.get("name") or "?"
            hp = enemy.get("hp")
            mx = enemy.get("max_hp")
            if isinstance(mx, int):
                full_hp[eid] = mx
            if isinstance(hp, int) and isinstance(mx, int):
                best[eid] = max(best.get(eid, 0), mx - hp)
        # An enemy that was there and now is not was killed, not forgotten.
        gone |= (present_now - seen)
        present_now = seen

    for eid in gone:
        if eid in full_hp:
            best[eid] = full_hp[eid]

    dealt = sum(best.values())
    return {
        "dealt": dealt,
        "rounds": rounds,
        "per_round": (dealt / rounds) if rounds else 0.0,
        "enemies": " + ".join(sorted({names[e] for e in names})),
    }


# --- the deck ----------------------------------------------------------------

STARTERS = {c["name"] for c in cards._DATA.get("starters", [])} or {"Strike", "Defend"}


def deck_shape(run: dict[str, Any]) -> dict[str, Any]:
    """Engine density and starter count, the mechanism recommendation 2 predicts.

    `deck` in a run row is the deck at death. Engine cards are whatever
    `cards.py` itself calls an engine (`cards.ENGINE_TAGS`), so this metric and
    the picking logic cannot drift apart.
    """
    names = [cards.base_name(n) for n in (run.get("deck") or [])]
    if not names:
        return {"size": run.get("deck_size") or 0, "engines": 0, "density": 0.0, "starters": 0}
    engines = 0
    for name in names:
        info = cards.card_info(name) or {}
        if set(info.get("tags") or ()) & cards.ENGINE_TAGS:
            engines += 1
    starters = sum(1 for n in names if n in STARTERS)
    return {"size": len(names), "engines": engines,
            "density": engines / len(names), "starters": starters}


BOSS_SNAPSHOT_KEY = "a1f17"


def boss_shape(run: dict[str, Any]) -> dict[str, Any]:
    """The deck the run took into the act 1 boss, if it was recorded.

    `deck_at_floor` is written by `RunRecorder` on the way past each act boss.
    Runs recorded before that existed have no such key, and report None rather
    than a zero -- a missing measurement is not a density of 0%.
    """
    snapshot = (run.get("deck_at_floor") or {}).get(BOSS_SNAPSHOT_KEY)
    if not snapshot:
        return {"size": None, "engines": None, "density": None}
    names = [cards.base_name(n) for n in snapshot]
    engines = sum(1 for n in names if cards.damage_engine_kind(n))
    return {"size": len(names), "engines": engines,
            "density": engines / len(names) if names else 0.0}


# --- assembly ----------------------------------------------------------------

def load_runs(runs_file: Path) -> list[dict[str, Any]]:
    if not runs_file.exists():
        return []
    out = []
    for line in runs_file.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def log_for(run: dict[str, Any], search_dirs: list[Path]) -> Optional[Path]:
    for path in logs_for(run, search_dirs):
        return path
    return None


def logs_for(run: dict[str, Any], search_dirs: list[Path]) -> list[Path]:
    """Every log this run was written to, in order.

    A run recorded normally names one. A run reconstructed by
    `recover_interrupted.py` can name dozens, because each relaunch started a
    new log -- so the fight that ended it may be in any of them.
    """
    named = run.get("decision_logs") or ([run["decision_log"]] if run.get("decision_log") else [])
    out = []
    for entry in named:
        name = Path(entry).name
        for directory in search_dirs:
            found = log_io.find_log(directory / name)
            if found:
                out.append(found)
                break
    return out


def rows_for_run(path: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    """Just this run's rows out of the session log.

    One decision log covers a whole session, so several runs name the same
    file. Without this every run in set 4 measured the *session's* last fight
    and returned an identical 7.2 dmg/round. The run row's own timestamps are
    the join: a couple of seconds of slack at each end covers the gap between
    the last logged decision and `RunRecorder.finish`.
    """
    start = (run.get("started_ts") or 0) - 2
    end = (run.get("ended_ts") or 0) + 5
    if not end:
        return list(log_io.rows(path))
    return [r for r in log_io.rows(path)
            if isinstance(r.get("ts"), (int, float)) and start <= r["ts"] <= end]


def measure(runs: list[dict[str, Any]], search_dirs: list[Path]) -> list[dict[str, Any]]:
    out = []
    for run in runs:
        path = log_for(run, search_dirs)
        record: dict[str, Any] = {
            "floor": run.get("floor_reached", 0),
            "act": run.get("act_reached", 0),
            "killed_by": " + ".join(e.get("name", "?") for e in
                                    (run.get("killed_by") or {}).get("enemies", [])) or "(unknown)",
            "dmg_taken_per_floor": (run.get("damage_taken", 0) / run["floor_reached"])
            if run.get("floor_reached") else 0.0,
            "log": path.name if path else None,
        }
        record["reconstructed"] = bool(run.get("reconstructed"))
        record.update(deck_shape(run))
        boss = boss_shape(run)
        record["boss_size"] = boss["size"]
        record["boss_engines"] = boss["engines"]
        record["boss_density"] = boss["density"]
        if path is None:
            record.update({"dealt": None, "rounds": None, "per_round": None, "enemies": "(log missing)"})
        else:
            mine: list[dict[str, Any]] = []
            for log_path in logs_for(run, search_dirs):
                mine.extend(rows_for_run(log_path, run))
            mine.sort(key=lambda r: r.get("ts") or 0)
            record["rows_in_window"] = len(mine)
            record.update(fight_damage(final_fight(mine)))
        out.append(record)
    return out


def group_of(record: dict[str, Any]) -> str:
    if record["act"] >= 2:
        return "reached act 2"
    if record["floor"] >= 17:
        return "lost the act 1 boss"
    return "died before the boss"


GROUP_ORDER = ("reached act 2", "lost the act 1 boss", "died before the boss")


def margin(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def _boss_density_cell(rows: list[dict[str, Any]]) -> str:
    """Mean damage-engine density at floor 17, or `--` if nothing recorded it.

    Printed with the number of runs it rests on, because early on that will be
    far fewer than `n` -- the snapshot only exists for runs played after it was
    added, and only for runs that actually reached floor 17.
    """
    have = [r["boss_density"] for r in rows if r.get("boss_density") is not None]
    if not have:
        return "--"
    return "%.0f%% (%d)" % (statistics.mean(have) * 100, len(have))


def report(label: str, records: list[dict[str, Any]], per_run: bool) -> None:
    usable = [r for r in records if r["per_round"]]
    print(f"\n=== {label} -- {len(records)} runs, {len(usable)} with a readable final fight ===")
    if not usable:
        print("  no decision logs found for these runs")
        return

    print(f"\n{'group':<22}{'n':>3}{'dmg/round':>19}{'median':>8}{'range':>14}"
          f"{'engines/deck':>14}{'dmg-eng@17':>12}{'dmg taken/floor':>17}")
    for group in GROUP_ORDER:
        rows_ = [r for r in usable if group_of(r) == group]
        if not rows_:
            continue
        pr = [r["per_round"] for r in rows_]
        dens = [r["density"] for r in rows_]
        taken = [r["dmg_taken_per_floor"] for r in rows_]
        print(f"{group:<22}{len(rows_):>3}"
              f"{statistics.mean(pr):>11.1f} (+/-{margin(pr):>3.1f})"
              f"{statistics.median(pr):>8.1f}"
              f"{min(pr):>6.1f}-{max(pr):<7.1f}"
              f"{statistics.mean(dens) * 100:>12.0f}%"
              f"{_boss_density_cell(rows_):>12}"
              f"{statistics.mean(taken):>17.1f}")

    pr_all = [r["per_round"] for r in usable]
    print(f"\n  whole set: {statistics.mean(pr_all):.1f} dmg/round (+/-{margin(pr_all):.1f}), "
          f"median {statistics.median(pr_all):.1f}")

    # The separation is the point: if the groups overlap, this metric is no
    # better than floor depth and the report should say so out loud.
    act2 = [r["per_round"] for r in usable if group_of(r) == "reached act 2"]
    lost = [r["per_round"] for r in usable if group_of(r) == "lost the act 1 boss"]
    if act2 and lost:
        if min(act2) > max(lost):
            print(f"  separated: the weakest act 2 run ({min(act2):.1f}) is above the "
                  f"strongest boss loss ({max(lost):.1f})")
        else:
            print(f"  OVERLAPPING: act 2 runs go down to {min(act2):.1f}, boss losses up "
                  f"to {max(lost):.1f} -- read this metric with the same caution as depth")

    if per_run:
        print(f"\n  {'floor':>5} {'dmg/rd':>7} {'rounds':>6} {'dealt':>6} {'deck':>5} "
              f"{'eng':>4} {'start':>5} {'@17':>8}  final fight")
        for r in sorted(records, key=lambda x: -(x["per_round"] or 0)):
            pr = f"{r['per_round']:.1f}" if r["per_round"] else "-"
            rounds = r["rounds"] if r["rounds"] else "-"
            dealt = r["dealt"] if r["dealt"] is not None else "-"
            at17 = ("%d/%d" % (r["boss_engines"], r["boss_size"])
                    if r.get("boss_size") else "-")
            print(f"  {r['floor']:>5} {pr:>7} {rounds:>6} {dealt:>6} {r['size']:>5} "
                  f"{r['engines']:>4} {r['starters']:>5} {at17:>8}  {r['enemies']} "
                  f"(killed by {r['killed_by']})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", help="an archive directory name, or a path to one")
    ap.add_argument("--all", action="store_true", help="every archived set with runs")
    ap.add_argument("--per-run", action="store_true", help="list every run")
    ap.add_argument("--with-interrupted", action="store_true",
                    help="include runs recovered by recover_interrupted.py "
                         "(runs_interrupted.jsonl), which no set's figures count")
    args = ap.parse_args()

    if args.all:
        targets = []
        for directory in sorted(ARCHIVE.iterdir()):
            if (directory / "runs.jsonl").exists():
                targets.append((directory.name, directory / "runs.jsonl", [directory, LOGS]))
        targets.append(("live set", LOGS / "runs.jsonl", [LOGS]))
    elif args.set:
        directory = Path(args.set)
        if not directory.is_dir():
            directory = ARCHIVE / args.set
        if not (directory / "runs.jsonl").exists():
            print(f"no runs.jsonl in {directory}")
            return 1
        targets = [(directory.name, directory / "runs.jsonl", [directory, LOGS])]
    else:
        targets = [("live set", LOGS / "runs.jsonl", [LOGS])]

    for label, runs_file, search_dirs in targets:
        runs = load_runs(runs_file)
        if args.with_interrupted:
            extra = load_runs(runs_file.parent / "runs_interrupted.jsonl")
            if extra:
                print(f"\n[{label}] including {len(extra)} reconstructed run(s) -- "
                      f"these are not part of any set's figures")
                runs = runs + extra
        if not runs:
            continue
        report(label, measure(runs, search_dirs), args.per_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
