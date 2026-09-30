"""Fixes found during set 4: the relaunch loop, resuming a paused run, the map
in the log, and Decimillipede segments that revive."""
import copy

from bot import loop as loop_mod
from bot.game_state import GameState
from bot.loop import BotLoop, _raw_for_replay
from bot.strategy import combat
from conftest import load_fixture


class _OkClient:
    def __init__(self, state):
        self.state = state
        self.posts = []

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        return self.state

    def post_action(self, action, **fields):
        self.posts.append((action, fields))
        return {"status": "ok"}


MENU = {"state_type": "menu", "menu_screen": "main", "ready_for_command": True,
        "options": [{"name": "continue"}, {"name": "abandon_run"}]}
EVENT = {"state_type": "event", "ready_for_command": True,
         "event": {"options": [{"index": 0, "is_proceed": True}]},
         "run": {"act": 1, "floor": 1}, "player": {"hp": 70, "max_hp": 70, "gold": 0}}
IN_RUN = {"state_type": "map", "ready_for_command": True, "run": {"act": 1, "floor": 8},
          "player": {"hp": 46, "max_hp": 70, "gold": 90}, "map": {"next_options": [], "nodes": []}}


# --- the relaunch loop --------------------------------------------------------

def test_a_main_menu_click_does_not_forgive_a_freeze(tmp_path, monkeypatch):
    # Set 4's start: "continue" succeeded, loaded straight back into a screen
    # the bot could not handle, and the reset kept every recovery at 1.
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _OkClient(MENU)
    loop = BotLoop(client=client, max_actions=1)
    loop._consecutive_recoveries = 1
    loop.run()
    loop.close()
    assert client.posts == [("menu_select", {"option": "continue"})]
    assert loop._consecutive_recoveries == 1


def test_real_progress_still_forgives_a_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    loop = BotLoop(client=_OkClient(EVENT), max_actions=1)
    loop._consecutive_recoveries = 1
    loop.run()
    loop.close()
    assert loop._consecutive_recoveries == 0


# --- resuming a paused run -----------------------------------------------------

def test_keep_run_continues_a_run_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setenv("STS2_KEEP_RUN", "1")
    relaunched = []
    monkeypatch.setattr(loop_mod, "kill_and_relaunch", lambda **kw: relaunched.append(kw))
    loop = BotLoop(client=_OkClient(IN_RUN), max_actions=0)
    loop._abandon_preexisting_run()
    loop.close()
    assert relaunched == [] and not loop._force_abandon


def test_without_keep_run_a_run_in_progress_is_still_abandoned(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.delenv("STS2_KEEP_RUN", raising=False)
    relaunched = []
    monkeypatch.setattr(loop_mod, "kill_and_relaunch", lambda **kw: relaunched.append(kw))
    loop = BotLoop(client=_OkClient(IN_RUN), max_actions=0)
    loop._abandon_preexisting_run()
    loop.close()
    assert relaunched and loop._force_abandon


# --- the whole map in the log ---------------------------------------------------

def test_map_decisions_are_logged_with_the_whole_map():
    # The routing fix for set 4, run 1 could not be checked against the real
    # choice: map rows carried only the next options, not the graph.
    nodes = [{"col": 0, "row": 1, "type": "Monster", "children": [[0, 2]]},
             {"col": 0, "row": 2, "type": "RestSite", "children": []}]
    gs = GameState({"state_type": "map", "run": {"act": 1, "floor": 1},
                    "player": {"hp": 12, "max_hp": 70, "gold": 0},
                    "map": {"nodes": nodes, "next_options": [{"index": 0, "col": 0, "row": 1, "type": "Monster"}]}})
    raw = _raw_for_replay(gs)
    assert raw["state_type"] == "map" and raw["map"]["nodes"] == nodes
    assert GameState(raw).map["nodes"] == nodes


# --- Decimillipede: segments that revive -----------------------------------------

REATTACH = {"id": "REATTACH_POWER", "name": "Reattach", "amount": 25, "type": "Buff",
            "description": "If other segments are still alive, revives in 2 turns with 25 HP."}
STRIKE = {"id": "STRIKE_SILENT", "name": "Strike", "type": "Attack", "cost": "1",
          "description": "Deal 6 damage.", "target_type": "AnyEnemy", "can_play": True, "keywords": []}


def _segment(i, hp):
    return {"entity_id": f"DECIMILLIPEDE_SEGMENT_{i}", "name": "Decimillipede", "hp": hp, "max_hp": 46,
            "block": 0, "status": [dict(REATTACH)],
            "intents": [{"type": "Attack", "label": "3",
                         "description": "This enemy intends to Attack for 3 damage."}]}


def _segments_turn(*hps, hp=60):
    raw = copy.deepcopy(load_fixture("live_gardener_first_strike.json")["raw"])
    raw["player"].update(hp=hp, max_hp=70, energy=1, block=0, status=[], potions=[],
                         hand=[dict(STRIKE, index=0)])
    raw["battle"]["enemies"] = [_segment(i, h) for i, h in enumerate(hps)]
    return GameState(raw)


def test_chip_damage_evens_the_segments_out():
    gs = _segments_turn(30, 44, 20)
    action, fields = combat.decide(gs)
    assert (action, fields.get("target")) == ("play_card", "DECIMILLIPEDE_SEGMENT_1")


def test_no_kill_that_the_revive_undoes():
    # The 5-HP segment would be back with 25 before the 44-HP one could fall.
    gs = _segments_turn(5, 44)
    action, fields = combat.decide(gs)
    assert fields.get("target") == "DECIMILLIPEDE_SEGMENT_1"


def test_the_kill_is_taken_when_the_others_can_follow():
    gs = _segments_turn(5, 20)
    action, fields = combat.decide(gs)
    assert fields.get("target") == "DECIMILLIPEDE_SEGMENT_0"


def test_the_kill_is_taken_when_the_hit_would_kill_us():
    raw_state = _segments_turn(5, 44, hp=5)
    action, fields = combat.decide(raw_state)
    assert fields.get("target") == "DECIMILLIPEDE_SEGMENT_0"
