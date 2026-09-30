"""Act 1 boss mechanics the bot was blind to.

Six of nine runs in one set died at floor 17 to an act 1 boss, and three
separate defects converged on that fight.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat

BECKON = {"name": "Beckon", "type": "Status", "cost": "1", "can_play": True,
          "description": "At the end of your turn, if this is in your Hand,  lose 6 HP."}
INFECTION = {"name": "Infection", "type": "Status", "cost": "0", "can_play": False,
             "description": "Unplayable. At the end of your turn, if this is in your Hand, take 3 damage."}
BUBBLE = {"name": "Bubble Bubble", "cost": "1", "type": "Skill", "can_play": True,
          "description": "If the enemy has Poison, apply 9 Poison.", "target_type": "AnyEnemy"}
STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "can_play": True,
          "description": "Deal 6 damage.", "target_type": "AnyEnemy"}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "can_play": True,
          "description": "Gain 5 Block.", "target_type": "Self"}

INTANGIBLE = {"name": "Intangible", "amount": 1,
              "description": "Reduce all damage taken and HP loss to 1. Lasts for 1 turn."}
POISON = {"name": "Poison", "amount": 5, "description": "Takes damage at the start of its turn."}


def _state(cards, enemy_status=None, hp=50):
    hand = [dict(c, index=i) for i, c in enumerate(copy.deepcopy(cards))]
    return GameState({
        "state_type": "boss", "run": {"act": 1, "floor": 17, "ascension": 0},
        "player": {"hp": hp, "max_hp": 70, "energy": 3, "block": 0, "status": [], "potions": [],
                   "hand": hand, "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{"entity_id": "B0", "name": "Soul Fysh", "hp": 120, "max_hp": 120,
                                "block": 0, "status": list(enemy_status or []),
                                "intents": [{"type": "Attack", "label": "16",
                                             "title": "", "description": ""}]}]},
    })


def test_beckon_hp_loss_is_counted_like_infection_damage():
    """Two wordings for one mechanic: "take 3 damage" vs "lose 6 HP". Only the
    first was matched, so Beckon -- 6 HP a turn, dealt 1-2 at a time by the
    boss -- was completely invisible."""
    assert combat._self_damage_in_hand(BECKON) == 6
    assert combat._self_damage_in_hand(INFECTION) == 3
    assert combat._hand_curse_damage([BECKON, INFECTION]) == 9


def test_bubble_bubble_is_a_dud_without_poison_on_the_target():
    assert combat._is_dud_this_turn(BUBBLE, _state([BUBBLE])) is True
    assert combat._is_dud_this_turn(BUBBLE, _state([BUBBLE], [POISON])) is False


def test_attacks_are_duds_while_the_enemy_is_intangible():
    """Intangible caps all damage at 1, so a hand of attacks becomes a couple
    of points. Those turns belong to Block and setup."""
    assert combat._is_dud_this_turn(STRIKE, _state([STRIKE], [INTANGIBLE])) is True
    assert combat._is_dud_this_turn(STRIKE, _state([STRIKE])) is False
    # A card that also blocks still earns its play.
    assert combat._is_dud_this_turn(DEFEND, _state([DEFEND], [INTANGIBLE])) is False


def test_blocking_is_preferred_over_attacking_into_intangible():
    gs = _state([STRIKE, DEFEND], [INTANGIBLE])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])
    assert played == "Defend"
