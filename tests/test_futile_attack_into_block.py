"""An attack that cannot break the target's Block does nothing.

Block resets each turn, so damage absorbed by it is simply gone. Reported
live: a 6-damage Strike thrown into 7 Block while the enemy was winding up a
13-damage hit. Poison bypasses Block, so a card carrying it still counts.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat

STRIKE = {"name": "Strike", "cost": "1", "type": "Attack",
          "description": "Deal 6 damage.", "target_type": "AnyEnemy", "can_play": True}
BLUR = {"name": "Blur", "cost": "1", "type": "Skill",
        "description": "Gain 5 Block. Block is not removed at the start of your next turn.",
        "target_type": "Self", "can_play": True}
POISON = {"name": "Deadly Poison", "cost": "1", "type": "Skill",
          "description": "Apply 5 Poison.", "target_type": "AnyEnemy", "can_play": True}


def _state(cards, enemy_block, attacking=False):
    hand = [dict(c, index=i) for i, c in enumerate(copy.deepcopy(cards))]
    intents = ([{"type": "Attack", "label": "13", "title": "", "description": ""}] if attacking
               else [{"type": "Buff", "label": "", "title": "", "description": "buffing"}])
    return GameState({
        "state_type": "monster", "run": {"act": 1, "floor": 8, "ascension": 0},
        "player": {"hp": 50, "max_hp": 70, "energy": 1, "block": 0, "status": [], "potions": [],
                   "hand": hand, "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{"entity_id": "E0", "name": "E", "hp": 40, "max_hp": 40,
                                "block": enemy_block, "status": [], "intents": intents}]},
    })


def _played(gs, fields):
    return next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])


def test_a_strike_that_cannot_break_block_loses_to_anything_useful():
    gs = _state([STRIKE, BLUR], enemy_block=7)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields) == "Blur"


def test_the_same_strike_is_preferred_when_it_can_get_through():
    gs = _state([STRIKE, BLUR], enemy_block=0)
    action, fields = combat.decide(gs)
    assert _played(gs, fields) == "Strike"


def test_poison_still_counts_because_it_ignores_block():
    gs = _state([POISON, BLUR], enemy_block=20)
    action, fields = combat.decide(gs)
    assert _played(gs, fields) == "Deadly Poison"


def test_a_futile_attack_is_still_played_when_it_is_the_only_option():
    # Energy does not carry over, so a wasted play costs nothing.
    gs = _state([STRIKE], enemy_block=20)
    action, _ = combat.decide(gs)
    assert action == "play_card"
