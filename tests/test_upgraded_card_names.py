"""Upgraded cards arrive with the upgrade in the name ("Afterimage+").

Every lookup in `cards.py` is keyed on the base name, so before this an
upgraded card matched nothing and scored 0.0 -- the bot went progressively
blind to its own best cards as a run upgraded them. Caught in a live run: on
a "Choose 2 cards to Remove" screen backed by a returns-them-upgraded relic,
every candidate tied at 0.0 upgrade value, so it picked the first card in the
list -- Strike+, already upgraded, gaining nothing.
"""
from bot.strategy import cards as card_db


def test_upgrade_marker_is_stripped_for_lookups():
    assert card_db.base_name("Strike+") == "Strike"
    assert card_db.base_name("Strike++") == "Strike"
    assert card_db.base_name("Dagger Throw+") == "Dagger Throw"
    assert card_db.base_name("Well-Laid Plans") == "Well-Laid Plans"


def test_upgraded_cards_keep_their_score():
    assert card_db.score_card("Afterimage+", {}) == card_db.score_card("Afterimage", {})
    assert card_db.score_card("Afterimage+", {}) > 0


def test_upgraded_starters_are_still_recognised_as_starters():
    # Otherwise Strike+ reads as an unknown real card, is protected from
    # removal, and a genuine card is cut in its place.
    deck = ["Strike+", "Strike", "Defend", "Afterimage"]
    plain = {"name": "Strike", "type": "Attack"}
    upgraded = {"name": "Strike+", "type": "Attack", "is_upgraded": True}
    real = {"name": "Afterimage", "type": "Skill"}

    assert card_db.removal_priority(upgraded, deck) >= 500
    assert card_db.removal_priority(upgraded, deck) > card_db.removal_priority(real, deck)
    # Between the two, cut the un-upgraded copy and keep the upgrade.
    assert card_db.removal_priority(plain, deck) > card_db.removal_priority(upgraded, deck)


def test_a_curse_is_cut_before_any_starter_even_without_a_type_field():
    # Live removal screens don't always report `type`; a curse that slips
    # through is one the bot will never cut.
    deck = ["Normality", "Strike", "Defend"]
    curse = {"name": "Normality", "rarity": "Curse"}
    starter = {"name": "Strike", "type": "Attack"}
    assert card_db.removal_priority(curse, deck) > card_db.removal_priority(starter, deck)


def test_upgrade_target_skips_already_upgraded_cards():
    offered = [
        {"name": "Strike+", "index": 0, "type": "Attack", "is_upgraded": True},
        {"name": "Normality", "index": 1, "type": "Curse"},
        {"name": "Blade Dance", "index": 2, "type": "Attack"},
    ]
    chosen = card_db.pick_upgrade_target(offered, [c["name"] for c in offered])
    assert chosen is not None
    assert chosen["name"] == "Blade Dance"


def test_upgrade_target_still_avoids_curses_and_starters():
    offered = [
        {"name": "Normality", "index": 0, "type": "Curse"},
        {"name": "Strike", "index": 1, "type": "Attack"},
        {"name": "Noxious Fumes", "index": 2, "type": "Skill"},
    ]
    chosen = card_db.pick_upgrade_target(offered, [c["name"] for c in offered])
    assert chosen["name"] == "Noxious Fumes"
