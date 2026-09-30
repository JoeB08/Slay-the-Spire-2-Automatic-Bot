"""Normal fights are the deck's supply line.

`Monster` scored 0.5 against an Elite's 6.0, so the bot beelined elites on a
starter deck. 45% of all recorded deaths are at floors 7-9 -- the first elite --
with a median deck of 14 cards. Early on, ordinary fights (each one a card
reward) are worth more than the elite they pay for.
"""
from bot.strategy import map_nav


def test_normal_fights_outrank_elites_until_the_deck_is_built():
    assert map_nav._monster_value(0) > map_nav._node_value("Elite", 1.0, 0, 0, 0)
    assert map_nav._monster_value(2) > map_nav._node_value("Elite", 1.0, 0, 0, 2)


def test_the_preference_reverses_once_enough_cards_are_banked():
    target = map_nav.EARLY_CARD_TARGET
    assert map_nav._monster_value(target) < map_nav._node_value("Elite", 1.0, 0, 0, target)


def test_monster_value_tapers_rather_than_switching():
    values = [map_nav._monster_value(n) for n in range(map_nav.EARLY_CARD_TARGET + 1)]
    assert values == sorted(values, reverse=True), "should decay smoothly as cards accumulate"
    assert values[-1] == map_nav.MONSTER_VALUE_BASE


def test_a_thin_deck_does_not_make_a_low_hp_elite_attractive():
    # The HP gate still dominates -- readiness needs both.
    assert map_nav._node_value("Elite", 0.3, 0, 0, 0) < 0
    assert map_nav._node_value("Elite", 0.3, 0, 0, 10) < 0
