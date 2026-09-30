"""The Merchant??? event: its "???" relics are judged by their text, then it is left."""
from bot import loop
from bot.game_state import GameState
from bot.strategy import shop


def _relic(index, name, price, text, affordable=True):
    return {"index": index, "category": "relic", "price": price, "is_stocked": True,
            "can_afford": affordable, "relic_id": "FAKE_" + name.upper().replace(" ", "_"),
            "relic_name": name + "???", "relic_description": text}


# The offer from the live payload (set 4, act 2 floor 25).
OFFER = [
    _relic(0, "Venerable Tea Set", 57,
           "Whenever you enter a Rest Site, start the next combat with an additional "
           "[silent_energy_icon.png]."),
    _relic(1, "Blood Vial", 47, "At the start of each combat, heal 1 HP."),
    _relic(2, "Mango", 51, "Upon pickup, raise your Max HP by 3."),
    _relic(3, "Snecko Eye", 50, "Start each combat Confused."),
    _relic(4, "Anchor", 57, "Start each combat with 4 Block."),
]


def _state(items, gold=135):
    return GameState({
        "state_type": "fake_merchant",
        "run": {"act": 2, "floor": 25, "ascension": 0},
        "fake_merchant": {"event_id": "FAKE_MERCHANT", "event_name": "The Merchant???",
                          "started_fight": False, "shop": {"can_proceed": False, "items": items}},
        "player": {"hp": 9, "max_hp": 80, "gold": gold, "potions": [], "max_potion_slots": 3},
    })


def test_the_fake_merchant_is_handled_not_polled():
    # Live: unhandled, this screen fell through to "state" and the stuck
    # detector relaunched the game over and over.
    action, _ = loop._dispatch(_state(OFFER))
    assert action in ("shop_purchase", "proceed")


def test_buys_the_best_relic_by_its_text():
    action, fields = shop.decide_fake_merchant(_state(OFFER))
    assert action == "shop_purchase"
    assert fields["index"] == 0  # the energy relic scores highest


def test_keeps_the_gold_reserve():
    # 90 gold: the 57-gold relics would leave 33, under the 40 reserve.
    action, fields = shop.decide_fake_merchant(_state(OFFER, gold=90))
    assert (action, fields) == ("shop_purchase", {"index": 1})


def test_never_buys_a_drawback_only_relic():
    only_snecko = [OFFER[3]]
    assert shop.decide_fake_merchant(_state(only_snecko)) == ("proceed", {})


def test_a_one_off_pickup_is_not_worth_the_gold():
    assert shop.decide_fake_merchant(_state([OFFER[2]])) == ("proceed", {})


def test_leaves_when_nothing_is_affordable():
    assert shop.decide_fake_merchant(_state(OFFER, gold=60)) == ("proceed", {})
