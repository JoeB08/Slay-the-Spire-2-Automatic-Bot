"""Cards that discard as a cost must be played last.

Dagger Throw ("Deal 6 damage. Draw 1 card. Discard 1 card.") played before a
Strike can make the discard eat the Strike. Reported on a Bullet Time turn --
"ALL cards in your Hand are free to play this turn. You cannot draw additional
cards" -- where the draw does nothing and the discard is pure cost, so
ordering is the only thing that matters.

The rule existed and had never run: `_forces_a_discard` referenced an
undefined `_DISCARD_N_RE` (NameError), and both `_play_timing` call sites
passed no `hand`, so the branch guarded by `hand is not None` was unreachable.
"""
from bot.strategy import combat

DAGGER_THROW = {"name": "Dagger Throw", "cost": "1", "index": 0,
                "description": "Deal 6 damage. Draw 1 card. Discard 1 card."}
STRIKE = {"name": "Strike", "cost": "1", "index": 1, "description": "Deal 6 damage."}
SLY_CARD = {"name": "Untouchable", "cost": "1", "index": 2, "description": "Sly. Gain 6 Block.",
            "keywords": [{"name": "Sly", "description":
                          "If this card is discarded from your Hand before the end of your turn, "
                          "play it for free."}]}
CALCULATED_GAMBLE = {"name": "Calculated Gamble", "cost": "0", "index": 3,
                     "description": "Discard your Hand, then draw that many cards."}


def test_the_helper_no_longer_raises_and_identifies_the_cost():
    assert combat._forces_a_discard(DAGGER_THROW) is True
    assert combat._forces_a_discard(STRIKE) is False


def test_discard_your_hand_is_a_different_effect_and_excluded():
    # That one wants a full hand, so it must not be shoved to the end.
    assert combat._forces_a_discard(CALCULATED_GAMBLE) is False


def test_a_discard_cost_card_is_ordered_after_the_cards_it_would_eat():
    hand = [DAGGER_THROW, STRIKE]
    assert combat._play_timing(DAGGER_THROW, hand) == combat.PLAY_LATE
    assert combat._play_timing(DAGGER_THROW, hand) < combat._play_timing(STRIKE, hand)


def test_but_goes_early_when_discarding_is_a_benefit():
    # A Sly card in hand turns the discard into a free play.
    hand = [DAGGER_THROW, STRIKE, SLY_CARD]
    assert combat._play_timing(DAGGER_THROW, hand) == combat.PLAY_EARLY
