"""Picking a card mid-combat is a this-turn decision, not a deck decision.

Live run, act 1 floor 7, 16/70 HP: a Skill Potion offered three cards and the
bot took Knife Trap, which the game itself annotated "(Plays 0 Shivs)" because
the exhaust pile was empty. It did nothing and was discarded immediately. The
alternative was 6 Block plus a Shiv. `decide_card_select` was ranking by
`score_card` -- name and pick rate -- with no view of the board.
"""
import copy

from bot.game_state import GameState
from bot.strategy import misc_screens
from conftest import load_fixture

MIRAGE = {"name": "Mirage", "index": 0,
          "description": "Gain Block equal to Poison on ALL enemies. Exhaust."}
CLOAK = {"name": "Cloak and Dagger", "index": 1,
         "description": "Gain 6 Block. Add 1 Shiv into your Hand."}
KNIFE_TRAP_DUD = {"name": "Knife Trap", "index": 2,
                  "description": "Play every Shiv in your Exhaust Pile on the enemy. (Plays 0 Shivs)"}


def _state(cards, in_combat=True):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    raw["state_type"] = "monster" if in_combat else "card_reward"
    raw["card_select"] = {
        "screen_type": "choose", "prompt": "Choose a card.",
        "cards": [copy.deepcopy(c) for c in cards],
        "preview_showing": False, "can_cancel": False, "can_confirm": False,
    }
    raw["player"]["hp"] = 16
    raw["player"]["status"] = []
    raw["player"]["potions"] = []
    return GameState(raw)


def _chosen(gs, fields):
    return next(c for c in gs.raw["card_select"]["cards"] if c["index"] == fields["index"])


def test_never_takes_a_card_the_game_says_does_nothing():
    gs = _state([MIRAGE, CLOAK, KNIFE_TRAP_DUD])
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    action, fields = misc_screens.decide_card_select(gs)
    assert action == "select_card"
    assert _chosen(gs, fields)["name"] != "Knife Trap"


def test_prefers_the_card_with_real_value_this_turn():
    gs = _state([MIRAGE, CLOAK, KNIFE_TRAP_DUD])
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    _, fields = misc_screens.decide_card_select(gs)
    # Cloak and Dagger is the only offer with unconditional Block; Mirage
    # scales off enemy Poison, of which there is none.
    assert _chosen(gs, fields)["name"] == "Cloak and Dagger"
