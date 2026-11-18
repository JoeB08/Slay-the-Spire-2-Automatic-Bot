"""A run already in progress at startup is discarded, not resumed.

A half-played run isn't a measurement of the current build -- it may have been
played by older code, by hand, or interrupted partway -- so counting it
pollutes the results.
"""
import json

from bot.game_state import GameState
from bot.loop import BotLoop
from bot.strategy import menu


class _MidRunClient:
    """Reports a run in progress, then (after the relaunch) the main menu."""

    def __init__(self):
        self.actions_posted = []
        self.relaunched = False

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        player = {
            "hp": 40, "max_hp": 70, "gold": 100, "potions": [], "max_potion_slots": 3,
            "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        }
        if not self.relaunched:
            return {
                "state_type": "map", "ready_for_command": True,
                "run": {"act": 1, "floor": 9},
                "map": {"next_options": [{"index": 0, "col": 0, "row": 1, "type": "Monster"}], "nodes": []},
                "player": player,
            }
        return {
            "state_type": "menu", "ready_for_command": True, "menu_screen": "main",
            "run": {"act": 1, "floor": 9},
            "options": ["continue", "abandon_run", "singleplayer", "settings", "quit"],
            "player": player,
        }

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        return {"status": "ok"}


def test_abandons_a_run_that_was_already_underway(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _MidRunClient()

    def _fake_relaunch(**kwargs):
        client.relaunched = True

    monkeypatch.setattr("bot.loop.kill_and_relaunch", _fake_relaunch)

    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    assert client.relaunched, "must relaunch to reach the menu -- there is no open-menu action"
    assert client.actions_posted == [("menu_select", {"option": "abandon_run"})]
    # It must NOT resume the old run.
    assert ("menu_select", {"option": "continue"}) not in client.actions_posted


def test_the_abandoned_run_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    client = _MidRunClient()
    monkeypatch.setattr("bot.loop.kill_and_relaunch", lambda **kw: setattr(client, "relaunched", True))

    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    # RunRecorder only writes at game_over, which an abandoned run never hits.
    assert not (tmp_path / "runs.jsonl").exists()


class _FreshMenuClient(_MidRunClient):
    """Already at the main menu with no run in progress."""

    def get_state(self):
        return {
            "state_type": "menu", "ready_for_command": True, "menu_screen": "main",
            "run": {"act": 1, "floor": 0},
            "options": ["singleplayer", "settings", "quit"],
            "player": {"hp": 0, "max_hp": 1, "gold": 0, "potions": [], "max_potion_slots": 3,
                       "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": []},
        }


class _MenuSaveClient(_MidRunClient):
    """At the main menu with a save to `continue` -- a game relaunched under a
    run, which is what every restart during the 11-13 stall came back to."""

    def get_state(self):
        return {
            "state_type": "menu", "ready_for_command": True, "menu_screen": "main",
            "run": {"act": 1, "floor": 0},
            "options": ["continue", "abandon_run", "singleplayer", "settings", "quit"],
            "player": {"hp": 0, "max_hp": 1, "gold": 0, "potions": [], "max_potion_slots": 3,
                       "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": []},
        }


def test_a_save_waiting_on_the_main_menu_is_abandoned(tmp_path, monkeypatch):
    """The 11-13 loop: 52 fresh processes each resumed the same stuck save.

    The API reports a plain main menu, so the in-a-run check above does not
    fire, and the menu handler's default is to continue. One relaunch further
    on it is still the same half-played run, and the same reason applies: it
    is not a measurement of this build.
    """
    monkeypatch.delenv("STS2_KEEP_RUN", raising=False)
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    relaunched = []
    monkeypatch.setattr("bot.loop.kill_and_relaunch", lambda **kw: relaunched.append(True))

    client = _MenuSaveClient()
    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    assert client.actions_posted == [("menu_select", {"option": "abandon_run"})]
    assert ("menu_select", {"option": "continue"}) not in client.actions_posted
    # Already at the menu -- nothing to relaunch for.
    assert not relaunched


def test_keep_run_still_resumes_a_save_on_the_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_KEEP_RUN", "1")
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _MenuSaveClient()
    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    assert client.actions_posted == [("menu_select", {"option": "continue"})]


def test_does_not_relaunch_when_no_run_is_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _FreshMenuClient()
    relaunched = []
    monkeypatch.setattr("bot.loop.kill_and_relaunch", lambda **kw: relaunched.append(True))

    menu._silent_selected_this_visit = False
    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    assert not relaunched, "a clean start must not restart the game"
    assert client.actions_posted == [("menu_select", {"option": "singleplayer"})]
