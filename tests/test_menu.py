from bot.game_state import GameState
from bot.strategy import menu
from conftest import load_fixture

CHARACTER_SELECT_ALL_ENABLED = {
    "state_type": "menu",
    "menu_screen": "character_select",
    "options": [
        {"name": "IRONCLAD", "enabled": True},
        {"name": "SILENT", "enabled": True},
        {"name": "REGENT", "enabled": True},
        {"name": "confirm", "enabled": True},
        {"name": "embark", "enabled": True},
        {"name": "back", "enabled": True},
    ],
}


def test_never_confirms_character_select_before_explicitly_choosing_silent():
    # Live bug: confirm/embark being enabled just means *some* character is
    # selected (the game had defaulted to Regent), not that Silent is. This
    # confirmed straight into a Regent run without ever picking Silent.
    menu._silent_selected_this_visit = False
    gs = GameState(CHARACTER_SELECT_ALL_ENABLED)
    action, fields = menu.decide_menu(gs)
    assert (action, fields) == ("menu_select", {"option": "SILENT"})


def test_confirms_only_after_explicitly_selecting_silent_this_visit():
    menu._silent_selected_this_visit = False
    gs = GameState(CHARACTER_SELECT_ALL_ENABLED)
    menu.decide_menu(gs)  # first call: selects Silent, sets the flag
    action, fields = menu.decide_menu(gs)  # second call: now safe to confirm
    assert (action, fields) == ("menu_select", {"option": "confirm"})


def test_selection_flag_resets_after_leaving_the_screen():
    menu._silent_selected_this_visit = True
    gs = GameState({"state_type": "menu", "menu_screen": "main", "options": ["singleplayer"]})
    menu.decide_menu(gs)
    assert menu._silent_selected_this_visit is False


def test_prefers_abandon_run_over_continue_when_flagged():
    gs = GameState(
        {
            "state_type": "menu",
            "menu_screen": "main",
            "options": ["continue", "abandon_run", "singleplayer", "multiplayer", "settings", "quit"],
        }
    )
    action, fields = menu.decide_menu(gs, prefer_abandon=True)
    assert (action, fields) == ("menu_select", {"option": "abandon_run"})


def test_continues_normally_when_not_flagged():
    gs = GameState(
        {
            "state_type": "menu",
            "menu_screen": "main",
            "options": ["continue", "abandon_run", "singleplayer", "multiplayer", "settings", "quit"],
        }
    )
    action, fields = menu.decide_menu(gs, prefer_abandon=False)
    assert (action, fields) == ("menu_select", {"option": "continue"})


def test_menu_with_no_actionable_options_polls_instead_of_proceeding():
    # `proceed` isn't valid on menu screens -- a live run produced a burst of
    # errors firing it at a transitional menu with no options.
    gs = GameState({"state_type": "menu", "menu_screen": "main", "options": []})
    action, fields = menu.decide_menu(gs)
    assert action == "state"


def test_game_over_with_no_options_polls_instead_of_proceeding():
    gs = GameState({"state_type": "game_over", "game_over": {"options": []}})
    action, fields = menu.decide_game_over(gs)
    assert action == "state"


def test_timeline_advances_when_unlocks_are_pending():
    gs = GameState(load_fixture("timeline_pending.json"))
    action, fields = menu.decide_menu(gs)
    assert (action, fields) == ("menu_select", {"option": "advance"})


def test_timeline_backs_out_once_nothing_is_pending():
    raw = load_fixture("timeline_pending.json")
    raw = {**raw, "obtained_unrevealed_count": 0}
    gs = GameState(raw)
    action, fields = menu.decide_menu(gs)
    assert (action, fields) == ("menu_select", {"option": "back"})


def test_game_over_reads_nested_options_not_top_level():
    # This exact payload caused a live infinite error loop: the code read
    # top-level gs.options (None here) instead of game_over.options, fell
    # through to "proceed" -- not a valid action on this screen -- and kept
    # erroring forever instead of returning to the main menu.
    gs = GameState(load_fixture("game_over.json"))
    action, fields = menu.decide_game_over(gs)
    assert action == "menu_select"
    assert fields == {"option": "main_menu"}
