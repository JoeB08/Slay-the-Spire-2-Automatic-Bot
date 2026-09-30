"""Two live discard misplays.

1. Cloak and Dagger ("Gain 6 Block. Add 1 Shiv into your Hand") classified as
   an attack because it makes a Shiv, so its 6 Block was invisible to the
   ranker and it was pitched ahead of a 5-Block Defend. Observed live as
   "Survivor discarding Cloak & Dagger then playing Defend".
2. Retain was not considered at all. A card without Retain is discarded at end
   of turn regardless, so pitching it costs nothing extra; a Retain card would
   have carried over for free.
"""
from bot.strategy.misc_screens import _discard_rank, _has_retain

RETAIN_KW = [{"name": "Retain", "description": "Retained cards are not discarded at the end of turn."}]
CLOAK = {"name": "Cloak and Dagger", "cost": "1",
         "description": "Gain 6 Block. Add 1 Shiv into your Hand.", "target_type": "Self"}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}


def _enemy(attack=10):
    return [{"entity_id": "E_0", "name": "E", "hp": 40, "max_hp": 40, "block": 0, "status": [],
             "intents": [{"type": "Attack", "label": str(attack), "title": "", "description": ""}]}]


def _rank(card, unblocked=10, energy=3):
    return _discard_rank(card, {}, energy, _enemy(), 0, unblocked, 0)


def test_a_card_that_blocks_and_attacks_keeps_its_block_value():
    # Both sit in the "block we need" tier; the tie-break is what each is
    # actually worth this turn, so the plain Defend is the one pitched.
    assert _rank(DEFEND) > _rank(CLOAK)
    assert max((CLOAK, DEFEND), key=_rank)["name"] == "Defend"


def test_retain_detection_reads_keyword_and_prefix():
    assert _has_retain({"name": "X", "keywords": RETAIN_KW}) is True
    assert _has_retain({"name": "Y", "description": "Retain. Apply 7 Poison."}) is True
    assert _has_retain(DEFEND) is False


def test_between_identical_cards_the_non_retain_one_is_pitched():
    plain = dict(DEFEND)
    retained = dict(DEFEND, keywords=RETAIN_KW)
    assert _rank(plain) > _rank(retained)
    assert max((plain, retained), key=_rank) is plain


def test_surplus_block_is_not_protected():
    """With three block cards against 5 unblocked damage, two are surplus --
    treating every one as precious is how a Retain Snakebite (7 poison) got
    pitched to keep a third redundant Defend."""
    from bot.strategy.misc_screens import _discard_rank

    snake = {"name": "Snakebite", "cost": "2", "index": 0, "keywords": RETAIN_KW,
             "description": "Retain. Apply 7 Poison.", "target_type": "AnyEnemy"}
    cloak = dict(CLOAK, index=1)
    d1 = dict(DEFEND, index=2, description="Gain 3 Block.")
    d2 = dict(DEFEND, index=3, description="Gain 3 Block.")
    hand = [snake, cloak, d1, d2]
    enemies = [{"entity_id": "E_0", "name": "E", "hp": 40, "max_hp": 40, "block": 0, "status": [],
                "intents": [{"type": "Attack", "label": "12", "title": "", "description": ""}]}]

    worst = max(hand, key=lambda c: _discard_rank(c, {}, 2, enemies, 0, 5, 0, hand))
    assert worst["name"] == "Defend", "should pitch surplus block, not the Retain card"
