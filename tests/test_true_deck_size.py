"""Deck size must exclude cards the fight generated.

A logged run reported `deck_size` 42, of which 20 were Shivs created during
combat -- the game's own counter showed roughly 22. Summing every pile is only
correct before anything has been played, when discard and exhaust are empty
and draw + hand is exactly the deck.
"""
from bot.game_state import GameState


def _state(draw, hand, discard=0, exhaust=0):
    return GameState({
        "state_type": "monster",
        "run": {"act": 1, "floor": 5, "ascension": 0},
        "player": {
            "hp": 60, "max_hp": 70, "energy": 3, "block": 0, "status": [], "potions": [],
            "draw_pile": [{"name": n} for n in draw],
            "hand": [{"name": n} for n in hand],
            "discard_pile": [], "exhaust_pile": [],
            "discard_pile_count": discard, "exhaust_pile_count": exhaust,
        },
        "battle": {"enemies": []},
    })


def test_pre_play_state_gives_the_true_deck():
    gs = _state(["Strike"] * 5 + ["Defend"] * 4, ["Neutralize", "Survivor"])
    assert gs.true_deck_names() == ["Strike"] * 5 + ["Defend"] * 4 + ["Neutralize", "Survivor"]
    assert len(gs.true_deck_names()) == 11


def test_mid_combat_state_is_refused_rather_than_guessed():
    # Anything discarded or exhausted means cards have been played, so tokens
    # may already exist and the sum is no longer the deck.
    assert _state(["Strike"], ["Shiv"], discard=3).true_deck_names() is None
    assert _state(["Strike"], ["Shiv"], exhaust=1).true_deck_names() is None


def test_it_is_the_reading_that_excludes_generated_shivs():
    mid = _state(["Shiv"] * 20 + ["Strike"] * 11, ["Shiv"] * 11, discard=5)
    assert mid.true_deck_names() is None          # refused
    assert len(mid.full_deck_names()) == 42       # the inflated number it replaces
