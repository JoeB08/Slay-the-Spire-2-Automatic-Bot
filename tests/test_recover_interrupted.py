"""Runs that never reached `game_over` and so were never recorded.

Twice this has dropped the most informative run of a set, so these tests pin
the two mistakes that made it invisible: treating a stretch of log as recorded
because it *overlapped* a recorded run (set 4's seventeenth run shared a
minute with the run before it), and merging fragments so loosely that
unrelated leftovers fused into runs that never happened.
"""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recover_interrupted.py"


def _load():
    spec = importlib.util.spec_from_file_location("ri", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ri = _load()


def row(ts, floor, hp=70, act=1):
    return {"ts": ts,
            "state": {"state_type": "map", "act": act, "floor": floor, "hp": hp},
            "raw": {"run": {"act": act, "floor": floor},
                    "player": {"hp": hp, "max_hp": 70}}}


def run_rows(start_ts, floors, hp=70, act=1, step=10):
    return [row(start_ts + i * step, f, hp, act) for i, f in enumerate(floors)]


def write_log(directory, name, rows):
    path = directory / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def write_runs(directory, runs):
    (directory / "runs.jsonl").write_text(
        "\n".join(json.dumps(r) for r in runs) + "\n" if runs else "", encoding="utf-8")


# --- splitting --------------------------------------------------------------

def test_a_new_run_starts_where_the_floor_resets():
    rows = run_rows(0, [1, 2, 3]) + run_rows(100, [1, 2])
    assert [len(g) for g in ri.split_runs(rows)] == [3, 2]


def test_a_long_silence_also_ends_a_run():
    # The rows handed to split_runs have had recorded runs removed, so what is
    # left can jump hours in a single step.
    rows = run_rows(0, [5, 6]) + run_rows(ri.MERGE_GAP_SECONDS * 3, [20, 21])
    assert [len(g) for g in ri.split_runs(rows)] == [2, 2]


def test_climbing_floors_stay_one_run():
    assert len(ri.split_runs(run_rows(0, [1, 2, 3, 17, 18, 31]))) == 1


# --- coverage ---------------------------------------------------------------

def test_a_recorded_run_is_not_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "LOGS", tmp_path)
    monkeypatch.setattr(ri, "ARCHIVE", tmp_path / "nothing")
    rows = run_rows(1000, list(range(1, 20)))
    write_log(tmp_path, "run_a.jsonl", rows)
    write_runs(tmp_path, [{"started_ts": 1000, "ended_ts": 1000 + 19 * 10}])
    assert ri.scan(tmp_path, tmp_path / "runs.jsonl") == []


def test_only_the_unrecorded_part_of_a_log_is_reported(tmp_path, monkeypatch):
    """Set 4's seventeenth run, in miniature.

    One stretch of log held the tail of a recorded run and the whole of an
    unrecorded one. Judged as a whole it overlapped a recorded window, so the
    fight that reframed the output gap stayed invisible.
    """
    monkeypatch.setattr(ri, "LOGS", tmp_path)
    monkeypatch.setattr(ri, "ARCHIVE", tmp_path / "nothing")
    recorded = run_rows(1000, [10, 11, 12, 13])          # ts 1000..1030
    lost = run_rows(1040, list(range(1, 18)))            # ts 1040..1200
    write_log(tmp_path, "run_a.jsonl", recorded + lost)
    write_runs(tmp_path, [{"started_ts": 1000, "ended_ts": 1035}])

    found = ri.scan(tmp_path, tmp_path / "runs.jsonl")
    assert len(found) == 1
    assert found[0]["floor_reached"] == 17
    assert found[0]["outcome"] == "interrupted"
    assert found[0]["reconstructed"] is True


# --- merging ----------------------------------------------------------------

def test_fragments_stalled_on_one_floor_become_one_run(tmp_path, monkeypatch):
    """The 11-13 stall: 60 relaunches, every one resuming act 3 floor 39."""
    monkeypatch.setattr(ri, "LOGS", tmp_path)
    monkeypatch.setattr(ri, "ARCHIVE", tmp_path / "nothing")
    write_runs(tmp_path, [])
    base = 1000
    for i in range(4):
        start = base + i * 200
        # Each restart: the menu at floor 0, then the save back on floor 39.
        rows = [row(start, 0, 0)] + run_rows(start + 10, [39] * 12, hp=13, act=3)
        write_log(tmp_path, f"run_{i}.jsonl", rows)

    found = ri.scan(tmp_path, tmp_path / "runs.jsonl")
    assert len(found) == 1, [f["floor_reached"] for f in found]
    assert found[0]["floor_reached"] == 39
    assert found[0]["act_reached"] == 3
    assert found[0]["fragments"] == 4


def test_separate_runs_are_not_fused(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "LOGS", tmp_path)
    monkeypatch.setattr(ri, "ARCHIVE", tmp_path / "nothing")
    write_runs(tmp_path, [])
    write_log(tmp_path, "run_0.jsonl", run_rows(1000, list(range(1, 15))))
    write_log(tmp_path, "run_1.jsonl", run_rows(1200, list(range(1, 22))))
    found = ri.scan(tmp_path, tmp_path / "runs.jsonl")
    assert sorted(f["floor_reached"] for f in found) == [14, 21]


def test_fragments_far_apart_in_time_are_not_fused(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "LOGS", tmp_path)
    monkeypatch.setattr(ri, "ARCHIVE", tmp_path / "nothing")
    write_runs(tmp_path, [])
    write_log(tmp_path, "run_0.jsonl", run_rows(1000, [31] * 12, act=2))
    write_log(tmp_path, "run_1.jsonl",
              run_rows(1000 + ri.MERGE_GAP_SECONDS * 4, [31] * 12, act=2))
    assert len(ri.scan(tmp_path, tmp_path / "runs.jsonl")) == 2


# --- the record -------------------------------------------------------------

def test_damage_is_summed_from_the_hp_drops_and_flagged_as_a_floor():
    group = run_rows(0, [1, 2, 3, 4])
    for r, hp in zip(group, (70, 60, 65, 40)):
        r["state"]["hp"] = hp
        r["raw"]["player"]["hp"] = hp
    record = ri.summarise(group, ["run_a.jsonl"])
    assert record["damage_taken"] == 10 + 25          # the heal is not damage
    assert record["damage_taken_is_a_floor"] is True
    assert record["hp_low_water"] == 40
    assert record["killed_by"] is None                # the run never ended
    assert record["deck"] == []


def test_a_menu_visit_is_not_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "LOGS", tmp_path)
    monkeypatch.setattr(ri, "ARCHIVE", tmp_path / "nothing")
    write_runs(tmp_path, [])
    write_log(tmp_path, "run_0.jsonl", [row(i, 0, 0) for i in range(30)])
    assert ri.scan(tmp_path, tmp_path / "runs.jsonl") == []
