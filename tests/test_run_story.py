"""The readable run story: built from the decision log, never able to stop a run."""
import json

from bot import run_story
from bot.game_state import GameState
from bot.run_recorder import RunRecorder

T0 = 1_000_000.0


def _combat_row(ts, rnd, hp, action, fields, hand, energy=3, block=0, enemies=None, floor=2):
    enemies = enemies or [{"entity_id": "NIBBIT_0", "name": "Nibbit", "hp": 20, "max_hp": 20,
                           "block": 0, "status": [],
                           "intents": [{"type": "Attack", "label": "8",
                                        "description": "This enemy intends to Attack for 8 damage."}]}]
    return {
        "ts": ts,
        "state": {"state_type": "monster", "act": 1, "floor": floor, "hp": hp, "max_hp": 70},
        "raw": {"state_type": "monster", "run": {"act": 1, "floor": floor},
                "player": {"hp": hp, "max_hp": 70, "energy": energy, "block": block, "status": [],
                           "hand": hand, "potions": []},
                "battle": {"round": rnd, "enemies": enemies}},
        "action": action, "fields": fields, "result": "ok",
    }


STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "description": "Deal 6 damage.",
          "can_play": True}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "description": "Gain 5 Block.",
          "can_play": True}


def _log(tmp_path):
    rows = [
        {"ts": T0, "state": {"state_type": "event", "act": 1, "floor": 1, "hp": 70, "max_hp": 70},
         "options": {"event": "Neow", "options": [{"index": 0, "title": "New Leaf"}]},
         "action": "choose_event_option", "fields": {"index": 0}, "result": "ok"},
        _combat_row(T0 + 1, 1, 70, "play_card", {"card_index": 0, "target": "NIBBIT_0"},
                    [dict(STRIKE), dict(DEFEND)]),
        # Ends the turn holding a Defend with 8 coming: flagged.
        _combat_row(T0 + 2, 1, 70, "end_turn", {}, [dict(DEFEND)], energy=2),
        _combat_row(T0 + 3, 2, 62, "end_turn", {}, [], energy=0),
        {"ts": T0 + 4, "state": {"state_type": "game_over", "act": 1, "floor": 2, "hp": 0, "max_hp": 70},
         "action": "menu_select", "fields": {"option": "main_menu"}, "result": "ok"},
    ]
    path = tmp_path / "run_test.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


RECORD = {
    "run_id": "abc", "started_ts": T0, "ended_ts": T0 + 4, "duration_s": 4, "outcome": "death",
    "act_reached": 1, "floor_reached": 2, "ascension": 0, "final_hp": 0, "max_hp": 70,
    "hp_low_water": 0, "damage_taken": 70, "decisions": 5,
    "killed_by": {"state_type": "monster", "floor": 2,
                  "enemies": [{"name": "Nibbit", "hp": 20, "max_hp": 20}]},
    "deck": ["Strike", "Strike", "Defend"], "deck_size": 3, "relics": ["Ring of the Snake"],
    "potions": ["Fire Potion"], "decision_log": "run_test.jsonl",
}


def test_story_reads_like_the_run(tmp_path):
    path = run_story.write_story(_log(tmp_path), RECORD, tmp_path / "stories")
    text = path.read_text(encoding="utf-8")
    assert "died on floor 2 to Nibbit" in text
    assert "Strike x2" in text
    assert "Neow: chose New Leaf" in text
    assert "Strike -> Nibbit" in text  # the target is named, not an id
    assert "The fight that ended the run" in text


def test_story_flags_block_left_in_hand_and_a_potion_carried_to_death(tmp_path):
    text = run_story.write_story(_log(tmp_path), RECORD, tmp_path / "stories").read_text(encoding="utf-8")
    assert "8 damage coming unblocked while holding Defend" in text
    assert "Died holding Fire Potion" in text


def test_a_turn_ended_by_the_kill_is_not_flagged():
    # Set 3, run 14: Strike + Neutralize finished Phrog Parasite; the end-turn
    # row held two Defends with no enemy standing, and was flagged anyway.
    row = _combat_row(T0, 4, 43, "end_turn", {}, [dict(DEFEND), dict(DEFEND)], energy=2)
    row["raw"]["battle"]["enemies"] = []  # the helper swaps an empty list for a live Nibbit
    assert run_story._end_turn_flag(row) is None
    dead = [{"entity_id": "PHROG_PARASITE_0", "name": "Phrog Parasite", "hp": 0, "max_hp": 60,
             "block": 0, "status": [], "intents": []}]
    row = _combat_row(T0, 4, 43, "end_turn", {}, [dict(DEFEND)], energy=2, enemies=dead)
    assert run_story._end_turn_flag(row) is None


def test_index_gets_one_line_per_run(tmp_path):
    out = tmp_path / "stories"
    run_story.write_story(_log(tmp_path), RECORD, out)
    run_story.write_story(_log(tmp_path), dict(RECORD, ended_ts=T0 + 60), out)
    index = (out / run_story.INDEX_FILE).read_text(encoding="utf-8")
    assert index.count("[story](") == 2
    assert "| 2 |" in index


def test_a_missing_log_still_gives_a_summary(tmp_path):
    text = run_story.write_story(tmp_path / "nope.jsonl", RECORD, tmp_path / "stories").read_text(
        encoding="utf-8")
    assert "decision log for this run was not found" in text


def test_the_recorder_writes_a_story_and_survives_a_broken_one(tmp_path, monkeypatch):
    _log(tmp_path)
    rec = RunRecorder(tmp_path, "run_test.jsonl")
    state = {"state_type": "monster", "run": {"act": 1, "floor": 2, "ascension": 0},
             "battle": {"is_play_phase": True, "enemies": []},
             "player": {"character": "The Silent", "hp": 40, "max_hp": 70, "gold": 0, "potions": [],
                        "max_potion_slots": 3, "relics": [], "hand": [], "draw_pile": [],
                        "discard_pile": [], "exhaust_pile": []}}
    rec.observe(GameState(state), "play_card", {})
    rec.observe(GameState(dict(state, state_type="game_over")), "menu_select", {})
    assert list((tmp_path / "stories").glob("*.md"))

    def _boom(*_a, **_k):
        raise RuntimeError("story failed")

    monkeypatch.setattr(run_story, "write_story", _boom)
    rec.observe(GameState(state), "play_card", {})
    rec.observe(GameState(dict(state, state_type="game_over")), "menu_select", {})  # must not raise
