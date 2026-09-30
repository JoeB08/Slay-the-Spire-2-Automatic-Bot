"""Draw before spending the rest of the turn.

Reported live: two energy, Backflip ("Gain 8 Block. Draw 2 cards") and Defend
in hand, and the bot spent the first energy on Defend. Both get played either
way, but drawing first expands what the second energy can be spent on --
drawing last is drawing into a turn that is already over.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat

BACKFLIP = {"name": "Backflip", "cost": "1", "type": "Skill", "can_play": True,
            "description": "Gain 8 Block. Draw 2 cards.", "target_type": "Self"}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "can_play": True,
          "description": "Gain 5 Block.", "target_type": "Self"}
SURVIVOR = {"name": "Survivor", "cost": "1", "type": "Skill", "can_play": True,
            "description": "Gain 8 Block. Discard 1 card.", "target_type": "Self"}


def _state(cards, energy=2, incoming=10):
    hand = [dict(c, index=i) for i, c in enumerate(copy.deepcopy(list(cards)))]
    return GameState({
        "state_type": "monster", "run": {"act": 1, "floor": 10, "ascension": 0},
        "player": {"hp": 60, "max_hp": 70, "energy": energy, "block": 0, "status": [],
                   "potions": [], "hand": hand, "draw_pile": [], "discard_pile": [],
                   "exhaust_pile": [], "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{"entity_id": "E0", "name": "E", "hp": 40, "max_hp": 40,
                                "block": 0, "status": [],
                                "intents": [{"type": "Attack", "label": str(incoming),
                                             "title": "", "description": ""}]}]},
    })


def test_a_draw_card_wants_to_go_early():
    assert combat._play_timing(BACKFLIP) == combat.PLAY_EARLY
    assert combat._play_timing(DEFEND) == combat.PLAY_NORMAL


def test_backflip_is_played_before_a_plain_defend():
    gs = _state([BACKFLIP, DEFEND])
    _, fields = combat.decide(gs)
    played = next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])
    assert played == "Backflip"


def test_a_discard_cost_card_still_goes_last():
    """Survivor draws nothing and discards as a cost -- playing it first
    throws away a card we were about to use."""
    assert combat._play_timing(SURVIVOR, [dict(SURVIVOR, index=0), dict(DEFEND, index=1)]) \
        == combat.PLAY_LATE
