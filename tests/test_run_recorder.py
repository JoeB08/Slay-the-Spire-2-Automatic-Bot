import json

from bot.game_state import GameState
from bot.run_recorder import RunRecorder


def _state(state_type="monster", floor=1, act=1, hp=70, max_hp=70, gold=99, enemies=None):
    return GameState(
        {
            "state_type": state_type,
            "run": {"act": act, "floor": floor, "ascension": 0},
            "battle": {"is_play_phase": True, "enemies": enemies or []},
            "player": {
                "character": "The Silent",
                "hp": hp, "max_hp": max_hp, "gold": gold,
                "potions": [], "max_potion_slots": 3, "relics": [{"name": "Ring of the Snake"}],
                "hand": [], "draw_pile": [{"name": "Strike"}, {"name": "Deadly Poison"}],
                "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def _recorder(tmp_path):
    return RunRecorder(tmp_path, "run_test.jsonl")


def test_writes_a_summary_line_when_a_run_ends(tmp_path):
    rec = _recorder(tmp_path)
    rec.observe(_state(floor=1, hp=70), "choose_map_node", {"index": 0})
    rec.observe(_state(floor=5, hp=40), "play_card", {"card_index": 0})
    rec.observe(_state(state_type="game_over", floor=5, hp=0), "menu_select", {"option": "main_menu"})

    runs_file = tmp_path / "runs.jsonl"
    assert runs_file.exists()
    record = json.loads(runs_file.read_text(encoding="utf-8").strip())
    assert record["outcome"] == "death"
    assert record["floor_reached"] == 5
    assert record["character"] == "The Silent"
    assert record["deck_size"] == 2


def test_tracks_damage_taken_and_low_water_hp(tmp_path):
    rec = _recorder(tmp_path)
    rec.observe(_state(floor=1, hp=70), "play_card", {})
    rec.observe(_state(floor=2, hp=50), "play_card", {})
    rec.observe(_state(floor=3, hp=62), "play_card", {})  # healed
    rec.observe(_state(state_type="game_over", floor=3, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["damage_taken"] == 20  # only the drop counts, not the heal
    assert record["hp_low_water"] == 50


def test_records_the_final_fight_as_killed_by(tmp_path):
    rec = _recorder(tmp_path)
    enemies = [{"entity_id": "E", "name": "Byrdonis", "hp": 40, "max_hp": 82, "status": [], "intents": []}]
    rec.observe(_state(state_type="elite", floor=7, hp=10, enemies=enemies), "play_card", {})
    rec.observe(_state(state_type="game_over", floor=7, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["killed_by"]["enemies"][0]["name"] == "Byrdonis"


def test_a_new_run_starts_when_the_floor_goes_backwards(tmp_path):
    rec = _recorder(tmp_path)
    rec.observe(_state(floor=8, hp=30), "play_card", {})
    rec.observe(_state(floor=1, hp=70), "play_card", {})  # new run began
    rec.observe(_state(state_type="game_over", floor=1, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    # The floor-8 progress belonged to the previous run, not this one.
    assert record["floor_reached"] == 1


def test_counts_screens_actions_and_recovery_events(tmp_path):
    rec = _recorder(tmp_path)
    rec.observe(_state(floor=1), "play_card", {})
    rec.observe(_state(state_type="map", floor=2), "choose_map_node", {})
    rec.note_event("cycle_detected")
    rec.observe(_state(state_type="game_over", floor=2, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["counts"]["screen:monster"] == 1
    assert record["counts"]["action:choose_map_node"] == 1
    assert record["recovery_events"] == 1


def test_deck_is_captured_from_mid_run_not_the_empty_game_over_state(tmp_path):
    # Card piles are only populated during combat and are empty by game_over,
    # so snapshotting the deck at the end recorded deck_size 0.
    rec = _recorder(tmp_path)
    rec.observe(_state(floor=3, hp=50), "play_card", {})

    empty_end = GameState(
        {
            "state_type": "game_over",
            "run": {"act": 1, "floor": 3, "ascension": 0},
            "player": {
                "character": "The Silent", "hp": 0, "max_hp": 70, "gold": 0,
                "potions": [], "max_potion_slots": 3, "relics": [],
                "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }
    )
    rec.observe(empty_end, "menu_select", {"option": "main_menu"})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["deck_size"] == 2
    assert record["deck"] == ["Deadly Poison", "Strike"]
    assert record["relics"] == ["Ring of the Snake"]


def test_menu_activity_does_not_start_a_run(tmp_path):
    rec = _recorder(tmp_path)
    rec.observe(_state(state_type="menu", floor=0), "menu_select", {"option": "singleplayer"})
    assert not (tmp_path / "runs.jsonl").exists()


def test_non_baseline_ascension_runs_are_kept_but_excluded_from_comparisons(tmp_path, monkeypatch):
    """A run at the wrong ascension is real data, but averaging it into the
    baseline would make harder enemies look like the bot getting worse. The
    API exposes no ascension control, so this can happen without the bot being
    able to prevent it -- the filter is the only guard."""
    from bot import relic_stats

    monkeypatch.setattr(relic_stats, "STATS_DIR", tmp_path)
    monkeypatch.setattr(relic_stats, "HISTORY_FILE", tmp_path / "h.jsonl")

    relic_stats.record_run(["Pomander"], 20, 2, "death", run_id="a0", ascension=0)
    relic_stats.record_run(["Pomander"], 3, 1, "death", run_id="a1", ascension=1)

    assert [e["run_id"] for e in relic_stats.load_history()] == ["a0"]
    assert {e["run_id"] for e in relic_stats.load_history(ascension=None)} == {"a0", "a1"}
    assert [e["run_id"] for e in relic_stats.load_history(ascension=1)] == ["a1"]
