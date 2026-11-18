"""The metric that is supposed to replace floor depth as the primary signal.

Floor depth cannot resolve a change any more: five consecutive sets land
between 18.8 and 24.1 with margins of +/-3.5 to +/-5.0. These tests pin the
parts of `damage_per_round.py` that a wrong answer would hide, the first of
which already bit once -- joining runs to logs by name alone made every run in
set 4 report the session's last fight, an identical 7.2 dmg/round for all
sixteen.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "damage_per_round.py"


def _load():
    spec = importlib.util.spec_from_file_location("dpr", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dpr = _load()


def battle_row(ts, floor, round_no, enemies):
    return {
        "ts": ts,
        "state": {"state_type": "combat", "act": 1, "floor": floor, "hp": 50},
        "raw": {"state_type": "combat", "run": {"act": 1, "floor": floor},
                "battle": {"round": round_no, "turn": "player", "enemies": enemies}},
    }


def enemy(eid, hp, max_hp, name="Soul Fysh"):
    return {"entity_id": eid, "name": name, "hp": hp, "max_hp": max_hp}


# --- which fight ------------------------------------------------------------

def test_the_final_fight_is_the_last_one_not_the_whole_log():
    rows = [
        battle_row(1, 8, 1, [enemy("A", 10, 40, "Wriggler")]),
        {"ts": 2, "state": {"state_type": "map", "floor": 9}},
        battle_row(3, 17, 1, [enemy("B", 200, 208)]),
        battle_row(4, 17, 2, [enemy("B", 150, 208)]),
    ]
    span = dpr.final_fight(rows)
    assert [r["ts"] for r in span] == [3, 4]


def test_a_floor_change_starts_a_new_fight_even_with_no_gap():
    rows = [battle_row(1, 16, 1, [enemy("A", 5, 30, "Scout")]),
            battle_row(2, 17, 1, [enemy("B", 208, 208)])]
    assert [r["ts"] for r in dpr.final_fight(rows)] == [2]


def test_no_combat_at_all_is_not_a_crash():
    assert dpr.final_fight([{"ts": 1, "state": {"floor": 3}}]) == []
    assert dpr.fight_damage([])["per_round"] == 0.0


# --- how much damage --------------------------------------------------------

def test_damage_is_the_most_ever_seen_on_each_enemy():
    span = [battle_row(1, 17, 1, [enemy("B", 208, 208)]),
            battle_row(2, 17, 2, [enemy("B", 108, 208)]),
            battle_row(3, 17, 3, [enemy("B", 8, 208)])]
    result = dpr.fight_damage(span)
    assert result["dealt"] == 200
    assert result["rounds"] == 3
    assert result["per_round"] == pytest.approx(200 / 3)


def test_an_enemy_that_vanished_mid_fight_was_killed():
    # Otherwise every killed minion is undercounted by its last-seen HP, and
    # a summoner fight reads as far less output than it was.
    span = [battle_row(1, 17, 1, [enemy("BOSS", 200, 200), enemy("MINION", 12, 30, "Kin")]),
            battle_row(2, 17, 2, [enemy("BOSS", 180, 200)])]
    assert dpr.fight_damage(span)["dealt"] == 20 + 30


def test_healing_does_not_reduce_the_damage_already_dealt():
    span = [battle_row(1, 17, 1, [enemy("B", 100, 200)]),
            battle_row(2, 17, 2, [enemy("B", 150, 200)])]
    assert dpr.fight_damage(span)["dealt"] == 100


# --- joining runs to logs ---------------------------------------------------

def test_rows_are_cut_to_the_run_that_asked_for_them(tmp_path):
    """One log holds a whole session, so several runs name the same file."""
    log = tmp_path / "run_session.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        battle_row(1000, 17, 1, [enemy("A", 100, 200)]),
        battle_row(1001, 17, 2, [enemy("A", 0, 200)]),
        battle_row(5000, 17, 1, [enemy("B", 190, 200)]),
        battle_row(5001, 17, 2, [enemy("B", 180, 200)]),
    ]) + "\n", encoding="utf-8")

    first = dpr.rows_for_run(log, {"started_ts": 1000, "ended_ts": 1001})
    second = dpr.rows_for_run(log, {"started_ts": 5000, "ended_ts": 5001})
    assert [r["ts"] for r in first] == [1000, 1001]
    assert [r["ts"] for r in second] == [5000, 5001]
    # The whole point: the two runs must not report the same fight.
    assert dpr.fight_damage(first)["dealt"] != dpr.fight_damage(second)["dealt"]


def test_a_run_with_no_timestamps_falls_back_to_the_whole_log(tmp_path):
    log = tmp_path / "run_x.jsonl"
    log.write_text(json.dumps(battle_row(1, 17, 1, [enemy("A", 1, 2)])) + "\n", encoding="utf-8")
    assert len(dpr.rows_for_run(log, {})) == 1


# --- grouping ---------------------------------------------------------------

@pytest.mark.parametrize("act,floor,expected", [
    (2, 33, "reached act 2"),
    (1, 17, "lost the act 1 boss"),
    (1, 8, "died before the boss"),
])
def test_runs_are_grouped_by_how_far_they_got(act, floor, expected):
    assert dpr.group_of({"act": act, "floor": floor}) == expected


def test_an_in_combat_selection_screen_does_not_cut_the_fight():
    """Prepared's discard, a potion's targeting, Cloak and Dagger.

    These report their own state_type, so no battle payload is kept for them.
    Treating that as the end of the fight measured only what followed the last
    one: a floor-8 fight whose enemy went 62 -> 44 in round one came out as 3
    damage over 9 rounds, and four of set 5's twenty runs were understated.
    """
    rows = [
        battle_row(1, 8, 1, [enemy("P", 62, 62, "Phrog Parasite")]),
        battle_row(2, 8, 1, [enemy("P", 44, 62, "Phrog Parasite")]),
        {"ts": 3, "state": {"state_type": "hand_select", "act": 1, "floor": 8, "hp": 50}},
        battle_row(4, 8, 2, [enemy("P", 20, 62, "Phrog Parasite")]),
    ]
    span = dpr.final_fight(rows)
    assert [r["ts"] for r in span] == [1, 2, 4]
    assert dpr.fight_damage(span)["dealt"] == 42


def test_a_restarted_round_counter_means_a_different_fight():
    rows = [battle_row(1, 17, 7, [enemy("A", 10, 200)]),
            battle_row(2, 17, 1, [enemy("B", 180, 200)]),
            battle_row(3, 17, 2, [enemy("B", 150, 200)])]
    assert [r["ts"] for r in dpr.final_fight(rows)] == [2, 3]


def test_the_same_floor_number_in_another_act_is_another_fight():
    rows = [battle_row(1, 17, 3, [enemy("A", 5, 208)])]
    rows[0]["state"]["act"] = 1
    rows[0]["raw"]["run"]["act"] = 1
    later = battle_row(2, 17, 1, [enemy("B", 300, 300)])
    later["state"]["act"] = 2
    later["raw"]["run"]["act"] = 2
    assert [r["ts"] for r in dpr.final_fight(rows + [later])] == [2]
