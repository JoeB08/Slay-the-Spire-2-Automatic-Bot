"""Deck size must count the deck, not what the enemy put in it.

A logged run reported a 23-card deck of which nine were `Infection`, shuffled
in by a Wriggler pack before the first play. The real deck was 14. Status cards
are enemy-generated and combat-scoped; curses from events genuinely belong.
"""
from bot.game_state import GameState


def _combat_state(draw, hand):
    return GameState({
        "state_type": "monster", "run": {"act": 1, "floor": 8, "ascension": 0},
        "player": {"hp": 50, "max_hp": 70, "energy": 3, "block": 0, "status": [], "potions": [],
                   "draw_pile": draw, "hand": hand, "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": []},
    })


def _card(name, type_="Attack"):
    return {"name": name, "type": type_}


def test_enemy_generated_status_cards_do_not_count_as_deck_cards():
    draw = [_card("Strike")] * 5 + [_card("Infection", "Status")] * 9
    hand = [_card("Defend", "Skill")] * 5
    deck = _combat_state(draw, hand).true_deck_names()
    assert "Infection" not in deck
    assert len(deck) == 10


def test_event_curses_still_count():
    """A curse taken from an event really is in the deck -- unlike a Status
    card, it is a consequence of a choice."""
    draw = [_card("Strike")] * 3 + [_card("Injury", "Curse")]
    deck = _combat_state(draw, [_card("Defend", "Skill")]).true_deck_names()
    assert "Injury" in deck
    assert len(deck) == 5
