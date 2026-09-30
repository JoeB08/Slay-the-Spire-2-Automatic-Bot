"""Status cards must never outrank attacking.

Regression introduced by the unknown-card fix. `Slimed` ("Draw 1 card.
Exhaust.") is playable and absent from the card data, so when unknown cards
started scoring a middling 30 instead of 0 it cleared the value step's >=20
gate. The bot spent turns exhausting Slimed rather than attacking.

Unknown-is-not-bad is right for a card *reward*, where the alternatives are
also real cards. A Status card in hand is known-bad by type.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat

SLIMED = {"name": "Slimed", "cost": "1", "type": "Status",
          "description": "Draw 1 card. Exhaust.", "target_type": "Self", "can_play": True}
STRIKE = {"name": "Strike", "cost": "1", "type": "Attack",
          "description": "Deal 6 damage.", "target_type": "AnyEnemy", "can_play": True}


def _state(cards, enemy_hp=30, attack=5):
    hand = [dict(c, index=i) for i, c in enumerate(copy.deepcopy(cards))]
    return GameState({
        "state_type": "monster", "run": {"act": 1, "floor": 5, "ascension": 0},
        "player": {"hp": 60, "max_hp": 70, "energy": 3, "block": 0, "status": [], "potions": [],
                   "hand": hand, "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{"entity_id": "E0", "name": "E", "hp": enemy_hp, "max_hp": 30,
                                "block": 0, "status": [],
                                "intents": [{"type": "Attack", "label": str(attack),
                                             "title": "", "description": ""}]}]},
    })


def _played(gs, fields):
    return next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])


def test_attacking_beats_exhausting_a_status_card():
    gs = _state([SLIMED, STRIKE])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields) == "Strike"


def test_several_status_cards_still_do_not_win():
    gs = _state([SLIMED, SLIMED, SLIMED, STRIKE])
    action, fields = combat.decide(gs)
    assert _played(gs, fields) == "Strike"
