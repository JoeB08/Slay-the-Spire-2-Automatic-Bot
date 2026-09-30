"""A build is defined by its best card, not by a headcount.

Counting tagged cards made Noxious Fumes -- pick rate 68, a Power, the poison
engine itself -- worth exactly as much as Deadly Poison at 23. One point each,
threshold two, so owning the single card that decides the build was not enough
to commit to it. Weighting by card strength fixes that without letting filler
lock in a lane.
"""
from bot.strategy import cards as c

STARTERS = ["Strike"] * 5 + ["Defend"] * 4 + ["Neutralize", "Survivor"]


def _arch(*extra):
    return c.dominant_archetype(STARTERS + list(extra))


def test_one_engine_card_is_a_direction_not_a_build():
    """Noxious Fumes alone used to commit the whole run to poison.

    Its weight (2.55) cleared the threshold by itself, so a single pick locked
    the archetype. One strong Power is a direction; the second on-theme card
    is what makes it real.
    """
    assert _arch("Noxious Fumes") is None
    assert _arch("Noxious Fumes", "Deadly Poison") == "poison"


def test_a_single_filler_card_does_not():
    assert _arch("Deadly Poison") is None
    assert _arch("Blade Dance") is None


def test_two_decent_cards_still_commit():
    assert _arch("Deadly Poison", "Bouncing Flask") == "poison"
    assert _arch("Blade Dance", "Leading Strike") == "shiv"


def test_cards_with_no_archetype_never_commit_one():
    assert _arch("Backflip") is None
    assert _arch("Backflip", "Blur") is None


def test_engine_tags_outweigh_plain_ones_at_equal_quality():
    plain = c._archetype_weight("Bouncing Flask")   # poison, no engine tag
    engine = c._archetype_weight("Noxious Fumes")   # poison + power
    assert engine > plain * c.ENGINE_TAG_WEIGHT * 0.9


# --- relics as archetype evidence -------------------------------------------

def _relic(name, description=""):
    return {"name": name, "description": description}


KUNAI = _relic("Kunai", "Every time you play 3 Attacks in a single turn, gain 1 Dexterity.")
SNECKO_SKULL = _relic("Snecko Skull", "Whenever you apply Poison, apply an additional 1 Poison.")
GORGET = _relic("Gorget", "At the start of each combat, gain 4 Plating.")


def test_relics_that_never_name_their_archetype_still_count():
    """Kunai and Shuriken read "3 Attacks in a single turn" -- that is shiv."""
    assert c._relic_archetype_scores([KUNAI]) == {"shiv": 1.0}
    assert c._relic_archetype_scores([SNECKO_SKULL]) == {"poison": 1.0}


def test_an_off_archetype_relic_contributes_nothing():
    assert c._relic_archetype_scores([GORGET]) == {}


def test_a_relic_alone_never_commits_the_build():
    assert c.dominant_archetype([], [SNECKO_SKULL]) is None
    assert c.dominant_archetype(["Blade Dance"], [KUNAI]) is None


def test_a_relic_breaks_a_tie_between_two_engines():
    split = ["Deadly Poison", "Bouncing Flask", "Blade Dance", "Leading Strike"]
    assert c.dominant_archetype(split, [KUNAI]) == "shiv"
    assert c.dominant_archetype(split, [SNECKO_SKULL]) == "poison"
