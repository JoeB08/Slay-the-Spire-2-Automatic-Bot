"""Two live-run misplays, pinned.

1. The bot never retained a card. One logged run was offered "Choose a card
   to Retain." 41 times and retained nothing all 41: `can_confirm` is true the
   moment an *optional* prompt opens, and the handler read that as "we've
   picked enough".
2. Forced to discard, it pitched a Sly attack whose damage the enemy's Block
   absorbed entirely, over a Sly *block* card that would have covered the
   incoming hit -- both are Sly, so both landed in the same tier, and the
   tie-break was card pick-rate, which knows nothing about the board.
"""
import copy

from bot.game_state import GameState
from bot.strategy import misc_screens
from conftest import load_fixture

SLY_KW = [
    {
        "name": "Sly",
        "description": "If this card is discarded from your Hand before the end of your turn, play it for free.",
    }
]

SLY_ATTACK = {
    "name": "Slice",
    "cost": "1",
    "description": "Sly. Deal 6 damage.",
    "keywords": SLY_KW,
    "target_type": "AnyEnemy",
}
SLY_BLOCK = {
    "name": "Backflip",
    "cost": "1",
    "description": "Sly. Gain 12 Block.",
    "keywords": SLY_KW,
    "target_type": "Self",
}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}
NIGHTMARE = {
    "name": "Nightmare",
    "cost": "3",
    "description": "Choose a card. Next turn, add 3 copies of that card into your Hand. Exhaust.",
    "target_type": "Self",
}
REGRET = {"name": "Regret", "type": "Curse", "cost": "-2", "description": "Unplayable.", "can_play": False}


def _enemy(entity_id="E_0", hp=99, block=0, attack=None):
    return {
        "entity_id": entity_id,
        "name": entity_id,
        "hp": hp,
        "max_hp": 99,
        "block": block,
        "status": [],
        "intents": (
            [{"type": "Attack", "label": str(attack), "title": "", "description": ""}]
            if attack
            else [{"type": "StatusCard", "label": "1", "title": "", "description": ""}]
        ),
    }


def _screen(cards, prompt, enemies=None, can_confirm=False, selected=None, block=0):
    raw = copy.deepcopy(load_fixture("hand_select_discard.json"))
    offered = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(offered):
        c["index"] = i
        c.setdefault("can_play", True)
    raw["hand_select"] = {
        "mode": "simple_select",
        "prompt": prompt,
        "cards": offered,
        "selected_cards": selected or [],
        "can_confirm": can_confirm,
    }
    raw["player"]["hand"] = offered
    raw["player"]["block"] = block
    raw["player"]["energy"] = 3
    raw["player"]["status"] = []
    raw["battle"]["enemies"] = enemies if enemies is not None else [_enemy()]
    return GameState(raw)


def _chosen(gs, fields):
    return next(c for c in gs.raw["hand_select"]["cards"] if c["index"] == fields["card_index"])


# --- retain ---

def test_retain_prompt_actually_retains_a_card():
    # The exact live screen: can_confirm true from the moment it opened.
    gs = _screen([DEFEND, NIGHTMARE], "Choose a card to Retain.", can_confirm=True)
    action, fields = misc_screens.decide_hand_select(gs)
    assert action == "combat_select_card"
    assert _chosen(gs, fields)["name"] == "Nightmare"


def test_retain_confirms_once_one_card_is_picked():
    gs = _screen(
        [DEFEND, NIGHTMARE],
        "Choose a card to Retain.",
        can_confirm=True,
        selected=[{"index": 1, "name": "Nightmare"}],
    )
    action, _ = misc_screens.decide_hand_select(gs)
    assert action == "combat_confirm_selection"


def test_retain_takes_two_when_the_prompt_allows_two():
    gs = _screen(
        [DEFEND, NIGHTMARE],
        "Choose up to 2 cards to Retain.",
        can_confirm=True,
        selected=[{"index": 1, "name": "Nightmare"}],
    )
    action, fields = misc_screens.decide_hand_select(gs)
    assert action == "combat_select_card"
    assert _chosen(gs, fields)["name"] == "Defend"


def test_retain_declines_rather_than_carry_a_curse_into_next_turn():
    gs = _screen([REGRET], "Choose a card to Retain.", can_confirm=True)
    action, _ = misc_screens.decide_hand_select(gs)
    assert action == "combat_confirm_selection"


def test_mandatory_prompt_still_confirms_when_it_says_it_can():
    # can_confirm on a non-optional prompt means "enough selected" -- the
    # retain fix must not turn that into another pick.
    gs = _screen([DEFEND, NIGHTMARE], "Choose a card to Discard.", can_confirm=True)
    action, _ = misc_screens.decide_hand_select(gs)
    assert action == "combat_confirm_selection"


# --- Sly pitch quality ---

def test_pitches_the_sly_block_when_its_free_play_stops_the_hit():
    # Enemy sits behind 20 Block, so the Sly attack's 6 damage lands nothing;
    # the Sly block card's free play covers the whole 12 coming back.
    gs = _screen(
        [SLY_ATTACK, SLY_BLOCK],
        "Choose a card to Discard.",
        enemies=[_enemy(hp=99, block=20, attack=12)],
    )
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Backflip"


def test_pitches_the_sly_attack_when_no_damage_is_coming_in():
    # Nothing incoming, enemy unblocked -- now the free play worth having is
    # the attack, and the block card would do nothing.
    gs = _screen(
        [SLY_ATTACK, SLY_BLOCK],
        "Choose a card to Discard.",
        enemies=[_enemy(hp=99, block=0)],
    )
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Slice"
