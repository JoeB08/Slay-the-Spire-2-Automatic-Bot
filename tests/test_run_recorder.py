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


# --- winning (2026-11-19) ------------------------------------------------
#
# The bot won on 2026-11-19 and the recorder filed it as "killed by Aeonglass".
# `outcome = "death" if gs.hp <= 0` could only ever say death, because the mod
# fills the game_over state with a fixed "Run ended." and an hp of 0 whether
# the run was won or lost. All 290 runs before that one are recorded as deaths
# for the same reason.
#
# The repaired version asks the game's own win counter. It is NOT allowed to
# infer a victory from the screens, because clearing the final boss is not the
# end of the run: The Architect follows, it can kill you, and a death there
# leaves a trace identical to the win -- boss cleared, rewards taken, event
# reached, HP still showing, then game_over. Inference separates a death from
# a non-death and stops there.


def _win_trace(rec):
    """Floor 48: kill the act-3 boss, take the rewards, reach The Architect."""
    rec.observe(_state(floor=46, act=3, hp=20), "choose_map_node", {"index": 0})
    rec.observe(_state(state_type="boss", floor=48, act=3, hp=2,
                       enemies=[{"name": "Aeonglass", "hp": 33, "max_hp": 512}]),
                "play_card", {"card_index": 0})
    rec.observe(_state(state_type="rewards", floor=48, act=3, hp=2), "proceed", {})
    rec.observe(_state(state_type="event", floor=48, act=3, hp=2), "proceed", {})
    rec.observe(_state(state_type="game_over", floor=48, act=3, hp=0),
                "menu_select", {"option": "main_menu"})


def _record(tmp_path):
    return json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())


def _profile(*win_counts):
    """A profile reader returning each count in turn: run start, then finish."""
    counts = list(win_counts)

    def read():
        return {"profile_id": 1,
                "characters": [{"id": "silent", "times_won": counts.pop(0)},
                               {"id": "ironclad", "times_won": 0}]}

    return read


def test_the_games_own_counter_calls_the_win(tmp_path):
    rec = RunRecorder(tmp_path, "run_test.jsonl", profile_reader=_profile(0, 1))
    _win_trace(rec)
    record = _record(tmp_path)
    assert record["outcome"] == "victory"
    assert record["outcome_source"] == "profile"
    assert record["killed_by"] is None
    assert record["final_hp"] == 2


def test_dying_at_the_architect_is_not_a_win(tmp_path):
    """The whole reason inference is not trusted here.

    Identical screens to the victory above -- final boss cleared, rewards
    taken, the end event reached, HP still showing at the last poll. The only
    thing that differs is that the game did not count a win.
    """
    rec = RunRecorder(tmp_path, "run_test.jsonl", profile_reader=_profile(0, 0))
    _win_trace(rec)
    record = _record(tmp_path)
    assert record["outcome"] != "victory"
    assert record["cleared_final_boss"] is True


def test_the_screens_alone_never_claim_a_victory(tmp_path):
    """No profile to ask: record what is known, not what is hoped."""
    rec = _recorder(tmp_path)
    _win_trace(rec)
    record = _record(tmp_path)
    assert record["outcome"] == "ended"
    assert record["outcome_source"] == "screens"
    assert record["cleared_final_boss"] is True


def test_an_unreadable_profile_never_invents_a_win(tmp_path):
    def boom():
        raise OSError("connection refused")

    rec = RunRecorder(tmp_path, "run_test.jsonl", profile_reader=boom)
    _win_trace(rec)
    assert _record(tmp_path)["outcome"] == "ended"


def test_hp_plays_no_part_in_deciding_a_win(tmp_path):
    """A win at 2 HP and a win at full HP are both wins."""
    for hp in (1, 2, 68):
        path = tmp_path / ("win_%d" % hp)
        path.mkdir()
        rec = RunRecorder(path, "run_test.jsonl", profile_reader=_profile(3, 4))
        rec.observe(_state(state_type="boss", floor=48, act=3, hp=hp,
                           enemies=[{"name": "Aeonglass", "hp": 1, "max_hp": 512}]),
                    "play_card", {})
        rec.observe(_state(state_type="rewards", floor=48, act=3, hp=hp), "proceed", {})
        rec.observe(_state(state_type="game_over", floor=48, act=3, hp=0), "menu_select", {})
        record = _record(path)
        assert record["outcome"] == "victory", "win at %d hp missed" % hp
        assert record["final_hp"] == hp


def test_dying_at_a_boss_is_still_a_death_and_still_names_it(tmp_path):
    rec = RunRecorder(tmp_path, "run_test.jsonl", profile_reader=_profile(0, 0))
    rec.observe(_state(state_type="boss", floor=17, hp=13,
                       enemies=[{"name": "Lagavulin Matriarch", "hp": 90, "max_hp": 180}]),
                "play_card", {"card_index": 0})
    rec.observe(_state(state_type="game_over", floor=17, hp=0),
                "menu_select", {"option": "main_menu"})
    record = _record(tmp_path)
    assert record["outcome"] == "death"
    assert record["final_hp"] == 0
    assert record["killed_by"]["enemies"][0]["name"] == "Lagavulin Matriarch"


def test_hp_still_showing_in_the_last_poll_is_not_a_victory(tmp_path):
    """Every real death looked like this: the last poll before game_over read
    1-15 HP, because the killing blow landed between two polls. Reading that
    as 'walked away alive' would turn all ten of the night's deaths into wins.
    """
    for hp in (1, 2, 13, 15):
        path = tmp_path / ("death_%d" % hp)
        path.mkdir()
        rec = RunRecorder(path, "run_test.jsonl")
        rec.observe(_state(state_type="monster", floor=25, hp=hp), "play_card", {})
        rec.observe(_state(state_type="game_over", floor=25, hp=0), "menu_select", {})
        assert _record(path)["outcome"] == "death", "hp %d read as a win" % hp


def test_clearing_an_earlier_boss_is_not_clearing_the_final_one(tmp_path):
    """Seven of the ten deaths got past the act-1 boss first."""
    rec = _recorder(tmp_path)
    rec.observe(_state(state_type="boss", floor=17, hp=40,
                       enemies=[{"name": "Kin Priest", "hp": 10, "max_hp": 200}]),
                "play_card", {})
    rec.observe(_state(state_type="rewards", floor=17, hp=40), "proceed", {})
    rec.observe(_state(state_type="monster", floor=25, act=2, hp=4), "play_card", {})
    rec.observe(_state(state_type="game_over", floor=25, act=2, hp=0), "menu_select", {})
    record = _record(tmp_path)
    assert record["outcome"] == "death"
    assert record["cleared_final_boss"] is False
