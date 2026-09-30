import copy

from bot.game_state import GameState
from bot.strategy import shop
from conftest import load_fixture


def _shop(mutate=None, gold=None, potions=None, deck=None):
    raw = copy.deepcopy(load_fixture("shop.json"))
    if gold is not None:
        raw["player"]["gold"] = gold
    if potions is not None:
        raw["player"]["potions"] = potions
    if deck is not None:
        raw["player"]["draw_pile"] = [{"name": n} for n in deck]
    if mutate:
        mutate(raw["shop"]["items"])
    return GameState(raw)


def _item(gs, index):
    return next(it for it in gs.shop["items"] if it["index"] == index)


def _with_accuracy_and_an_affordable_relic(items):
    for it in items:
        if it["category"] == "relic":
            it["can_afford"] = True
    items.append({"index": 99, "category": "card", "price": 39, "is_stocked": True,
                  "can_afford": True, "card_name": "Accuracy",
                  "card_description": "Shivs deal 4 additional damage."})


SHIV_DECK = ["Strike"] * 4 + ["Defend"] * 4 + ["Blade Dance", "Leading Strike", "Cloak and Dagger"]


def test_a_key_card_for_the_deck_s_engine_comes_before_a_relic():
    # Floor 29, set 3: 153 gold went on Blood Vial and a removal, leaving 12
    # for a 39-gold Accuracy in a deck of Shiv makers. The user's rule: a
    # strong card for the archetype comes first.
    gs = _shop(mutate=_with_accuracy_and_an_affordable_relic, deck=SHIV_DECK)
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert fields["index"] == 99


def test_a_payoff_with_nothing_to_pay_off_waits_behind_the_relic():
    gs = _shop(mutate=_with_accuracy_and_an_affordable_relic,
               deck=["Strike"] * 5 + ["Defend"] * 5 + ["Deadly Poison"])
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["category"] == "relic"


def test_buys_card_removal_when_no_relic_is_affordable():
    # Every relic in the fixture is `can_afford: False`, so removal is the
    # top affordable priority here.
    #
    # Live bug: the code read `type`/`item_type`, but the real field is
    # `category` -- nothing matched, so the bot proceeded past every shop
    # without buying anything.
    gs = _shop()
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["category"] == "card_removal"


def test_skips_removal_when_deck_has_no_junk_to_remove():
    gs = _shop(deck=["Catalyst", "Footwork", "Adrenaline"])
    action, fields = shop.decide_shop(gs)
    if action == "shop_purchase":
        assert _item(gs, fields["index"])["category"] != "card_removal"


def test_buys_a_relic_when_removal_is_unavailable():
    def drop_removal(items):
        for it in items:
            if it["category"] == "card_removal":
                it["is_stocked"] = False
            if it["category"] == "relic":
                it["can_afford"] = True

    gs = _shop(mutate=drop_removal)
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["category"] == "relic"


def test_respects_can_afford_flag():
    def nothing_affordable(items):
        for it in items:
            it["can_afford"] = False

    gs = _shop(mutate=nothing_affordable)
    action, _ = shop.decide_shop(gs)
    assert action == "proceed"


def test_does_not_buy_potions_when_belt_is_full():
    def only_potions(items):
        for it in items:
            it["can_afford"] = it["category"] == "potion"
            if it["category"] != "potion":
                it["is_stocked"] = False

    gs = _shop(mutate=only_potions, potions=[{"id": "A"}, {"id": "B"}, {"id": "C"}])
    action, _ = shop.decide_shop(gs)
    assert action == "proceed"


def test_buys_a_potion_when_a_slot_is_free():
    def only_potions(items):
        for it in items:
            it["can_afford"] = it["category"] == "potion"
            if it["category"] != "potion":
                it["is_stocked"] = False

    gs = _shop(mutate=only_potions, potions=[])
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["category"] == "potion"


def test_proceeds_when_shop_is_empty():
    gs = _shop(mutate=lambda items: items.clear())
    action, _ = shop.decide_shop(gs)
    assert action == "proceed"


def test_a_relic_outranks_both_removal_and_cards():
    """Reported live: "why did it buy war paint, it should have bought horn
    cleat, removal and then cards".

    A shop relic is a one-time offer that leaves with the shop; removal
    recurs at later shops, and the junk it would remove is still there next
    time. Order is relic -> removal -> card.
    """
    def afford_one_relic(items):
        for it in items:
            if it["category"] == "relic":
                it["can_afford"] = it["price"] == 189

    gs = _shop(mutate=afford_one_relic, gold=200)
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["category"] == "relic"


def test_removal_still_wins_once_the_relics_are_gone():
    def buy_out_relics(items):
        for it in items:
            if it["category"] == "relic":
                it["is_stocked"] = False

    gs = _shop(mutate=buy_out_relics)
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["category"] == "card_removal"


def test_a_shop_discount_relic_is_bought_before_anything_else():
    """Membership Card makes every later purchase cheaper, so buying it after
    anything else wastes the discount on the items already paid for."""
    def add_membership(items):
        for it in items:
            if it["category"] == "relic":
                it["can_afford"] = True
        items.append({"index": 99, "category": "relic", "name": "Membership Card",
                      "price": 120, "can_afford": True, "is_stocked": True,
                      "description": "Cards, relics and potions in shops cost 50% less."})

    gs = _shop(mutate=add_membership, gold=400)
    action, fields = shop.decide_shop(gs)
    assert action == "shop_purchase"
    assert _item(gs, fields["index"])["name"] == "Membership Card"


def test_a_discount_relic_is_recognised_by_its_effect_text():
    """The real payload wording has never been captured, so name matching
    alone is not enough."""
    def add_unnamed(items):
        for it in items:
            if it["category"] == "relic":
                it["can_afford"] = True
        items.append({"index": 98, "category": "relic", "name": "Odd Coin",
                      "price": 90, "can_afford": True, "is_stocked": True,
                      "description": "Everything in shops is 30% off."})

    gs = _shop(mutate=add_unnamed, gold=400)
    action, fields = shop.decide_shop(gs)
    assert _item(gs, fields["index"])["name"] == "Odd Coin"


def test_an_ordinary_relic_does_not_look_like_a_discount():
    gs = _shop()
    ordinary = {"category": "relic", "name": "Gorget",
                "description": "At the start of each combat, gain 4 Plating."}
    assert shop._discounts_the_shop(ordinary) is False
