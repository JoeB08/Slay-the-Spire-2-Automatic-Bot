"""The 11-13 stall: one screen the bot could not satisfy, 3h35m, 52 relaunches.

Run 23 reached act 3 floor 39 and hit the Crystal Sphere divination screen.
The handler probed four candidate action names, the game rejected all of them,
the give-up path reset the probe counter, and the probe started over -- so the
path meant to stop the spin was the spin. The freeze detector then relaunched
the game, the fresh process resumed the save from the main menu, and it all
happened again, sixty times. No run finished for the rest of the night.

The probe is gone: the mod's own help text names the protocol, and none of the
four candidates was in it. What is covered here now:
  * the mod's rejection message is reported instead of being dropped,
  * the screen is played the way the mod documents it,
  * a screen that rejects everything ends the run instead of resuming into it,
  * the error streak counts screens, not decisions.
"""
import pytest

from bot.api_client import ApiError, _error_detail
from bot.game_state import GameState
from bot.loop import ERROR_STREAK_UNPLAYABLE, BotLoop, _screen_fingerprint
from bot.strategy import misc_screens

PLAYER = {"hp": 13, "max_hp": 70, "gold": 173, "potions": [], "max_potion_slots": 3,
          "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": []}

SPHERE = {
    "state_type": "crystal_sphere",
    "ready_for_command": True,
    "run": {"act": 3, "floor": 39},
    "player": PLAYER,
    "crystal_sphere": {
        "can_proceed": False,
        "grid_width": 11,
        "grid_height": 11,
        "clickable_cells": [{"x": 5, "y": 5}, {"x": 4, "y": 5}],
    },
}


class _RejectingClient:
    """Answers state, rejects every action -- the live screen's behaviour."""

    def __init__(self, state=None):
        self.state = state or SPHERE
        self.posts = []

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        return self.state

    def post_action(self, action, **fields):
        self.posts.append((action, fields))
        raise ApiError(f"{action}({fields}) -> rejected")


# --- fix 1: say what the mod said --------------------------------------------

def test_the_rejection_message_is_reported_when_the_mod_sends_one():
    assert _error_detail({"status": "error", "message": "no"}) == "no"


@pytest.mark.parametrize("key", ["error", "reason", "detail", "description"])
def test_other_keys_are_read_too(key):
    assert _error_detail({"status": "error", key: "unknown action"}) == "unknown action"


def test_a_body_with_no_known_key_is_reported_whole():
    # This is the case that cost two runs: every Crystal Sphere rejection
    # logged "-> None" because only `message` was read, so the one line that
    # would name the right action was thrown away.
    detail = _error_detail({"status": "error", "valid_actions": ["divine_small"]})
    assert "valid_actions" in detail and "divine_small" in detail


def test_an_empty_body_says_so_rather_than_none():
    assert _error_detail({"status": "error"}) == "no detail in the reply"
    assert "None" not in _error_detail({"status": "error"})


# --- fix 3: play the screen the way the mod documents it ---------------------
#
#     Use `crystal_sphere_set_tool(tool)` with `big` or `small`, then
#     `crystal_sphere_click_cell(x, y)`.
#
# -- the help text inside STS2_MCP.dll. Not one of the four names the bot used
# to probe appears in the mod at all; each came back "Unknown action".

def _sphere(**overrides):
    sphere = dict(SPHERE["crystal_sphere"])
    sphere.update(overrides)
    state = dict(SPHERE)
    state["crystal_sphere"] = sphere
    return GameState(state)


def test_the_tool_is_selected_before_anything_is_clicked():
    gs = _sphere(tool="small", can_use_big_tool=True, can_use_small_tool=True)
    action, fields = misc_screens.decide_crystal_sphere(gs)
    assert action == "crystal_sphere_set_tool"
    assert fields == {"tool": misc_screens.CRYSTAL_PREFERRED_TOOL}


def test_a_cell_is_clicked_once_the_tool_is_already_right():
    gs = _sphere(tool=misc_screens.CRYSTAL_PREFERRED_TOOL,
                 can_use_big_tool=True, can_use_small_tool=True)
    action, fields = misc_screens.decide_crystal_sphere(gs)
    assert action == "crystal_sphere_click_cell"
    assert set(fields) == {"x", "y"}


def test_the_other_tool_is_used_when_the_preferred_one_cannot_be():
    gs = _sphere(tool=None, can_use_big_tool=False, can_use_small_tool=True)
    action, fields = misc_screens.decide_crystal_sphere(gs)
    assert action == "crystal_sphere_set_tool"
    assert fields == {"tool": "small"}


def test_no_tool_is_requested_when_the_payload_does_not_describe_tools():
    """Rather than refuse to play a screen whose shape changed under us."""
    action, _fields = misc_screens.decide_crystal_sphere(GameState(SPHERE))
    assert action == "crystal_sphere_click_cell"


def test_the_most_central_clickable_tile_is_chosen():
    gs = _sphere(clickable_cells=[{"x": 0, "y": 0}, {"x": 5, "y": 5}, {"x": 10, "y": 1}])
    _action, fields = misc_screens.decide_crystal_sphere(gs)
    assert (fields["x"], fields["y"]) == (5, 5)


def test_the_cell_grid_is_used_when_there_is_no_clickable_list():
    """`clickable_cells` is derived, and derived fields are what mod updates
    drop. The full grid carries `is_clickable` on every cell."""
    gs = _sphere(clickable_cells=None,
                 cells=[{"x": 0, "y": 0, "is_clickable": False},
                        {"x": 6, "y": 5, "is_clickable": True}])
    action, fields = misc_screens.decide_crystal_sphere(gs)
    assert action == "crystal_sphere_click_cell"
    assert (fields["x"], fields["y"]) == (6, 5)


def test_the_divinations_are_spent_before_proceeding():
    """`crystal_sphere_proceed` is rejected while `can_proceed` is false, which
    is the rejection that started the whole stall."""
    assert misc_screens.decide_crystal_sphere(GameState(SPHERE))[0] != "crystal_sphere_proceed"
    assert misc_screens.decide_crystal_sphere(_sphere(can_proceed=True))[0] == "crystal_sphere_proceed"


def test_a_screen_with_nothing_clickable_is_left_rather_than_spun_on():
    gs = _sphere(clickable_cells=[], cells=[], can_proceed=False)
    assert misc_screens.decide_crystal_sphere(gs)[0] == "proceed"


# --- fix 2: a screen that rejects everything ends the run --------------------

def test_the_error_streak_counts_the_screen_not_the_decision(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _RejectingClient()
    loop = BotLoop(client=client, max_actions=4)
    loop.run()
    loop.close()

    # Every rejected poll counts towards the one screen. This used to be
    # asserted by checking that several *different* actions were sent, which
    # only held because the probe varied its action each time; a handler that
    # knows what it is doing repeats itself, so what matters is that the streak
    # tracks the screen and not whatever was last tried on it.
    assert len(client.posts) == 4
    assert loop._error_streak == 4


def test_an_unplayable_screen_abandons_instead_of_resuming(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    relaunched = []
    monkeypatch.setattr("bot.loop.kill_and_relaunch",
                        lambda **kw: relaunched.append(kw.get("reason")))

    client = _RejectingClient()
    loop = BotLoop(client=client, max_actions=1)
    loop._error_streak = ERROR_STREAK_UNPLAYABLE
    loop._error_streak_fp = _screen_fingerprint(GameState(SPHERE))
    loop._recover_from_stuck(GameState(SPHERE))
    loop.close()

    assert relaunched == ["unplayable_screen"]
    assert loop._force_abandon, "the save must be abandoned, not resumed into"
    # The freeze grace is for a frozen engine. This screen will reject the same
    # actions after a resume, so it must not spend one of those attempts.
    assert loop._consecutive_recoveries == 0


def test_a_freeze_with_no_rejections_still_gets_its_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    relaunched = []
    monkeypatch.setattr("bot.loop.kill_and_relaunch",
                        lambda **kw: relaunched.append(kw.get("reason")))

    frozen = {"state_type": "combat", "ready_for_command": True,
              "run": {"act": 1, "floor": 4}, "player": PLAYER,
              "combat": {"enemies": [], "player": PLAYER}}
    loop = BotLoop(client=_RejectingClient(frozen), max_actions=1)
    loop._recover_from_stuck(GameState(frozen))
    loop.close()

    assert relaunched == ["stuck_recovery"]
    assert not loop._force_abandon, "one freeze still gets to try resuming"
    assert loop._consecutive_recoveries == 1
