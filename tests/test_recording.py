"""The recording switch: a copy without the marker writes nothing at all.

That is the whole contract, and it is what makes this project safe to clone --
somebody who just wants to watch the bot play should not end up with a folder of
decision logs, stories and stats they never asked for. See `bot/recording.py`.

The thing these tests are really protecting is the other half of that contract:
the switch must not reach the *decisions*. So each one checks that the bot still
did its work (the run was tracked, the deck was remembered) and only the writing
stopped.
"""
import json

import pytest

from bot import deck_memory, recording, relic_stats
from bot.game_state import GameState
from bot.loop import BotLoop
from bot.run_recorder import RunRecorder


def _state(state_type="monster", floor=1, hp=70, cards=("Strike", "Deadly Poison")):
    return GameState(
        {
            "state_type": state_type,
            "run": {"act": 1, "floor": floor, "ascension": 0},
            "battle": {"is_play_phase": True, "enemies": []},
            "player": {
                "character": "The Silent",
                "hp": hp, "max_hp": 70, "gold": 99,
                "potions": [], "max_potion_slots": 3,
                "relics": [{"name": "Ring of the Snake"}],
                "hand": [], "draw_pile": [{"name": n} for n in cards],
                "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


class _IdleClient:
    """Enough of ApiClient for BotLoop to be built and stopped."""

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        return {
            "state_type": "event",
            "ready_for_command": True,
            "event": {"options": [{"index": 0, "is_proceed": True}]},
            "run": {"act": 1, "floor": 1},
            "player": {"hp": 70, "max_hp": 70, "gold": 0},
        }

    def post_action(self, action, **fields):
        return {}


# --- the switch itself -----------------------------------------------------

def test_the_marker_file_decides_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(recording.ENV_VAR, raising=False)
    monkeypatch.setattr(recording, "marker_path", lambda: tmp_path / recording.MARKER)
    assert recording.enabled() is False

    (tmp_path / recording.MARKER).write_text("on", encoding="utf-8")
    assert recording.enabled() is True


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("on", True), ("YES", True),
    ("0", False), ("false", False), ("off", False), ("no", False),
])
def test_the_environment_overrides_the_marker(value, expected, tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "marker_path", lambda: tmp_path / recording.MARKER)
    monkeypatch.setenv(recording.ENV_VAR, value)
    assert recording.enabled() is expected


def test_an_unrecognised_value_falls_back_to_the_marker(tmp_path, monkeypatch):
    """A typo must not silently turn recording off in the copy that measures."""
    monkeypatch.setattr(recording, "marker_path", lambda: tmp_path / recording.MARKER)
    (tmp_path / recording.MARKER).write_text("on", encoding="utf-8")
    monkeypatch.setenv(recording.ENV_VAR, "please")
    assert recording.enabled() is True


# --- what stops being written ---------------------------------------------

def test_no_decision_log_is_opened(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_RECORD", "0")
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path / "logs")

    loop = BotLoop(client=_IdleClient(), max_actions=1)
    loop._log({"event": "something_happened"})
    loop.close()

    assert not (tmp_path / "logs").exists(), "a fresh clone must not create logs/"


def test_the_run_summary_story_and_relic_history_are_all_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_RECORD", "0")
    monkeypatch.setattr(relic_stats, "STATS_DIR", tmp_path / "stats")
    monkeypatch.setattr(relic_stats, "HISTORY_FILE", tmp_path / "stats" / "relic_history.jsonl")

    rec = RunRecorder(tmp_path, "run_test.jsonl")
    rec.observe(_state(floor=1), "choose_map_node", {"index": 0})
    rec.observe(_state(floor=5, hp=40), "play_card", {"card_index": 0})
    rec.observe(_state(state_type="game_over", floor=5, hp=0), "menu_select", {"option": "main_menu"})

    assert list(tmp_path.iterdir()) == [], "nothing at all should have been written"


def test_a_finished_run_still_resets_so_the_next_one_is_clean(tmp_path, monkeypatch):
    """The bookkeeping has to keep working, or run 2 inherits run 1's floor."""
    monkeypatch.setenv("STS2_RECORD", "0")

    rec = RunRecorder(tmp_path, "run_test.jsonl")
    rec.observe(_state(floor=9), "play_card", {})
    rec.observe(_state(state_type="game_over", floor=9, hp=0), "menu_select", {})
    assert rec.run_id is None and rec.max_floor == 0

    rec.observe(_state(floor=1), "play_card", {})
    assert rec.max_floor == 1


def test_the_deck_is_still_remembered_just_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_RECORD", "0")
    cache = tmp_path / "deck_memory.json"
    monkeypatch.setattr(deck_memory, "_cache_path", lambda: cache)

    gs = _state(cards=("Strike", "Neutralize", "Deadly Poison"))
    deck_memory.remember(gs)

    assert not cache.exists()
    # the point of deck_memory: the deck is known outside combat too
    assert deck_memory.current_deck(_state(state_type="shop", cards=())) == [
        "Strike", "Neutralize", "Deadly Poison"
    ]


def test_nothing_is_appended_to_the_relic_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_RECORD", "0")
    monkeypatch.setattr(relic_stats, "STATS_DIR", tmp_path)
    monkeypatch.setattr(relic_stats, "HISTORY_FILE", tmp_path / "relic_history.jsonl")

    relic_stats.record_run(relics=["Ring of the Snake"], floor_reached=17, act_reached=1,
                           outcome="death", run_id="abc")

    assert not (tmp_path / "relic_history.jsonl").exists()


def test_the_relaunch_log_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_RECORD", "0")
    from bot import recovery

    monkeypatch.setattr(recovery, "RELAUNCH_LOG", tmp_path / "stats" / "relaunch_log.txt")
    recovery._note_relaunch("test")

    assert not (tmp_path / "stats").exists()


# --- and the positive control ---------------------------------------------

def test_with_recording_on_everything_is_written(tmp_path, monkeypatch):
    """The mirror of the tests above: the same calls, marker present."""
    monkeypatch.setenv("STS2_RECORD", "1")
    monkeypatch.setattr(relic_stats, "STATS_DIR", tmp_path / "stats")
    monkeypatch.setattr(relic_stats, "HISTORY_FILE", tmp_path / "stats" / "relic_history.jsonl")

    rec = RunRecorder(tmp_path, "run_test.jsonl")
    rec.observe(_state(floor=1), "choose_map_node", {"index": 0})
    # the depth reached is what was seen while the run was live; the game_over
    # payload's own floor does not count towards it
    rec.observe(_state(floor=5, hp=40), "play_card", {"card_index": 0})
    rec.observe(_state(state_type="game_over", floor=5, hp=0), "menu_select", {})

    runs_file = tmp_path / "runs.jsonl"
    assert runs_file.exists()
    assert json.loads(runs_file.read_text(encoding="utf-8").strip())["floor_reached"] == 5
    assert (tmp_path / "stats" / "relic_history.jsonl").exists()
