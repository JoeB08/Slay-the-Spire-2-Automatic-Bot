"""Event options that reshape the deck (notes 13, 14, 15)."""
from bot import deck_memory
from bot.game_state import GameState
from bot.strategy import events

STARTER_HEAVY = ["Strike"] * 5 + ["Defend"] * 5 + ["Neutralize", "Survivor"]
GOOD_DECK = ["Adrenaline", "Afterimage", "Footwork", "Noxious Fumes", "Envenom", "Prepared"]


def _opt(index, title, description="", **kw):
    return {"index": index, "title": title, "description": description,
            "is_locked": False, "is_proceed": False, **kw}


def _event(options, hp=70, max_hp=70, deck=()):
    gs = GameState(
        {
            "state_type": "event", "run": {"act": 1, "floor": 8},
            "event": {"in_dialogue": False, "options": list(options)},
            "player": {
                "hp": hp, "max_hp": max_hp, "gold": 100, "potions": [], "max_potion_slots": 3,
                "hand": [], "draw_pile": [{"name": n} for n in deck],
                "discard_pile": [], "exhaust_pile": [],
            },
        }
    )
    deck_memory.remember(gs)
    return gs


REMOVE_ONE = _opt(0, "Precise Scissors", "Remove 1 card from your Deck.")
GOLD = _opt(1, "Take the coins", "Gain 50 Gold.")
TRANSFORM = _opt(0, "Let Go", "Transform a card in your Deck.")
UPGRADE = _opt(1, "Maintain Control", "Upgrade a card in your Deck.")


def test_removal_is_preferred_over_plain_gold_when_the_deck_is_starter_heavy():
    gs = _event([REMOVE_ONE, GOLD], deck=STARTER_HEAVY)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 0


def test_removal_of_two_cards_beats_removal_of_one():
    two = _opt(0, "Precise Shears", "Remove 2 cards from your Deck.")
    one = _opt(1, "Precise Scissors", "Remove 1 card from your Deck.")
    gs = _event([two, one], deck=STARTER_HEAVY)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 0


def test_transform_preferred_while_the_deck_is_mostly_starters():
    gs = _event([TRANSFORM, UPGRADE], deck=STARTER_HEAVY)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 0


def test_upgrade_preferred_once_the_deck_holds_good_cards():
    # The reported behaviour was Transform *every time*; with a deck worth
    # improving, upgrading should win instead.
    gs = _event([TRANSFORM, UPGRADE], deck=GOOD_DECK)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_removal_still_refused_when_the_hp_cost_would_be_fatal():
    costly = _opt(0, "Precarious Shears", "Remove 2 cards from your Deck. Lose 16 HP.")
    leave = _opt(1, "Leave", "", is_proceed=True)
    gs = _event([costly, leave], hp=18, deck=STARTER_HEAVY)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_removal_worth_an_hp_cost_when_healthy():
    costly = _opt(0, "Precarious Shears", "Remove 2 cards from your Deck. Lose 16 HP.")
    gs = _event([costly, GOLD], hp=70, deck=STARTER_HEAVY)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 0


def test_no_deck_knowledge_means_no_shaping_bonus():
    assert events._deck_shaping_bonus("Remove 1 card from your Deck.", []) == 0.0
