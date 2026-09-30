"""Shop screen.

Verified against a real payload (see tests/fixtures/shop.json). Items live in
`shop.items`, and the type field is **`category`** -- not `type`/`item_type`,
which is what an earlier guess used: nothing ever matched, every branch fell
through, and the bot walked out of every shop without buying anything.

Categories seen live: `card`, `relic`, `potion`, `card_removal`. The API also
supplies `is_stocked` / `can_afford` per item, which are more trustworthy than
recomputing affordability from price and gold.

Priority: card removal first (thinning a Silent deck full of starter
Strikes/Defends is the biggest consistency lever), then relics, then cards
worth their price, then potions.
"""
from __future__ import annotations

import re

from typing import Any

from .. import deck_memory
from ..game_state import GameState
from . import cards as card_db

# Cards worth removing if we're paying for a removal at all.
JUNK_CARDS = {"Strike", "Defend"}
# Don't spend the last of our gold on a merely-decent card; relics and
# removals in later shops are usually the better use.
CARD_PURCHASE_GOLD_RESERVE = 40
MIN_CARD_SCORE_TO_BUY = 35
# Don't spend *past* a relic we could nearly afford. `can_afford` is a boolean,
# so "5 gold short" and "500 gold short" look identical -- a live shop had 205
# gold with Punch Dagger at 210 and Horn Cleat at 231 on the shelf, and the bot
# bought three cards for 161 gold, putting both permanently out of reach. Gold
# does not restock, and a relic is worth more than two cheap cards.
RELIC_SAVING_GAP = 60


# Relic scoring. There was none: the bot bought the *cheapest* affordable
# relic, which took War Paint ("Upon pickup, Upgrade 2 random Skills", 195)
# over Horn Cleat ("At the start of your 2nd turn, gain 14 Block", 231).
#
# The distinction that matters is one-time versus every-combat. A relic that
# fires each fight compounds over the rest of the run; a one-off pickup effect
# is a single card reward wearing a relic's price tag.
_RECURRING_HINTS = (
    "each combat", "every combat", "start of your", "start of each",
    "whenever", "each turn", "every turn", "at the end of",
)
_ONE_TIME_HINTS = ("upon pickup",)
_STRONG_HINTS = ("energy",)  # an extra energy per turn dwarfs everything else

RECURRING_BONUS = 14.0
ONE_TIME_PENALTY = -8.0
STRONG_BONUS = 10.0
RELIC_BASE_VALUE = 10.0


def relic_value(item: dict[str, Any]) -> float:
    """Rough worth of a shop relic, from its text rather than its price."""
    text = (item.get("relic_description") or item.get("description") or "").lower()
    score = RELIC_BASE_VALUE
    if any(h in text for h in _RECURRING_HINTS):
        score += RECURRING_BONUS
    elif any(h in text for h in _ONE_TIME_HINTS):
        score += ONE_TIME_PENALTY
    if any(h in text for h in _STRONG_HINTS):
        score += STRONG_BONUS
    return score


def _buyable(items: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [
        it
        for it in items
        if (it.get("category") or "").lower() == category
        and it.get("is_stocked", True)
        and it.get("can_afford", False)
    ]


def _price(item: dict[str, Any]) -> int:
    try:
        return int(item.get("price") or 0)
    except (TypeError, ValueError):
        return 0


def _card_name(item: dict[str, Any]) -> str:
    return item.get("card_name") or item.get("name") or ""


# Relics that make the rest of the shop cheaper have to be bought FIRST or
# their whole value is thrown away -- every purchase made ahead of one is paid
# at full price. Membership Card is the canonical case. Matched by name and by
# effect text, since the exact wording has not been captured in a payload yet.
_DISCOUNTS_SHOP_NAMES = ("membership card", "courier")
_DISCOUNTS_SHOP_RE = re.compile(
    r"(?:cheaper|less|discount|reduce[sd]?)[^.]*\b(?:shop|store|price)"
    r"|\b(?:shop|store|card|item)[^.]*(?:cost|price)[^.]*(?:less|cheaper)"
    r"|\d+% off",
    re.IGNORECASE,
)


def _discounts_the_shop(item: dict[str, Any]) -> bool:
    if (item.get("category") or "").lower() != "relic":
        return False
    name = (item.get("name") or "").lower()
    if any(hint in name for hint in _DISCOUNTS_SHOP_NAMES):
        return True
    text = item.get("description") or item.get("text") or ""
    return bool(_DISCOUNTS_SHOP_RE.search(text))


# The Merchant??? (act 2 event, its own state_type `fake_merchant`) sells "???"
# relics: weaker copies of real ones at about 50 gold -- Anchor??? "Start each
# combat with 4 Block", Blood Vial??? "At the start of each combat, heal 1 HP"
# -- and at least one that is only a drawback: Snecko Eye??? "Start each
# combat Confused." with no draw. The user's call: "it should look at the
# relics offered". They are judged like shop relics, by their text; a
# drawback-only relic is never bought, a one-off pickup effect is not worth
# the gold, and the card reserve is kept for the real shops ahead.
_DRAWBACK_RE = re.compile(
    r"\b(?:confused|curse|wound|lose \d+|take \d+ (?:more |additional )?damage)", re.IGNORECASE
)
_BENEFIT_RE = re.compile(
    r"\b(?:gain|heal|block|energy|draw|strength|dexterity|max hp|upgrade|raise|deal)\b"
    r"|energy_icon",
    re.IGNORECASE,
)
FAKE_RELIC_MIN_VALUE = RELIC_BASE_VALUE + RECURRING_BONUS  # acts every combat, or better


def _drawback_only(item: dict[str, Any]) -> bool:
    text = item.get("relic_description") or item.get("description") or ""
    return bool(_DRAWBACK_RE.search(text)) and not _BENEFIT_RE.search(text)


def decide_fake_merchant(gs: GameState) -> tuple[str, dict[str, Any]]:
    items = ((gs.raw.get("fake_merchant") or {}).get("shop") or {}).get("items") or []
    worth_it = [
        it for it in _buyable(items, "relic")
        if not _drawback_only(it)
        and relic_value(it) >= FAKE_RELIC_MIN_VALUE
        and gs.gold - _price(it) >= CARD_PURCHASE_GOLD_RESERVE
    ]
    if worth_it:
        best = max(worth_it, key=lambda it: (relic_value(it), -_price(it)))
        return "shop_purchase", {"index": best["index"]}
    # `proceed` works even while the shop reports can_proceed false.
    return "proceed", {}


# A key card for an engine the deck is already feeding comes before relics --
# the user's call. Accuracy ("Shivs deal 4 additional damage") is the case: weak
# on its own, very strong behind a handful of Shiv makers, and it can be the
# card that turns a leaning deck into a shiv deck. The relic-first order spent
# a floor-29 shop's 153 gold on Blood Vial and a removal, leaving 12 for a
# 39-gold Accuracy in a deck holding Blade Dance, Leading Strike and Nunchaku.
# `score_card` cannot see this -- it rates Accuracy 36-67 against Prepared's
# 116 -- so the rule keys on the card data's payoff tags and on the enablers
# already in the deck, not on the score. See `cards.is_key_payoff`, which card
# rewards use too.
def _is_key_card(item: dict[str, Any], deck_names: list[str], counts: dict[str, int]) -> bool:
    """A payoff card whose engine the deck already has 2+ enablers for."""
    if (item.get("category") or "").lower() != "card":
        return False
    return card_db.is_key_payoff(_card_name(item), deck_names, counts)


def decide_shop(gs: GameState) -> tuple[str, dict[str, Any]]:
    items = gs.shop.get("items") or []
    if not items:
        return "proceed", {}

    deck_names = deck_memory.current_deck(gs)

    # 0. A shop-discount relic outranks everything, including other relics:
    # buying it first is what makes every later purchase cheaper, so any item
    # bought ahead of it is money burned.
    discounters = _buyable(items, "relic")
    discounters = [it for it in discounters if _discounts_the_shop(it)]
    if discounters:
        return "shop_purchase", {"index": min(discounters, key=_price)["index"]}

    # 0.5. A key card for an engine the deck is feeding -- see `_is_key_card`.
    tag_counts = card_db.deck_tag_counts(deck_names)
    key_cards = [it for it in _buyable(items, "card") if _is_key_card(it, deck_names, tag_counts)]
    if key_cards:
        return "shop_purchase", {"index": min(key_cards, key=_price)["index"]}

    # 1. Relics first. A shop relic is a one-time offer that is gone with the
    # shop; card removal recurs at later shops and at some events, and the
    # junk being removed is still removable next time. Buying a card ahead of
    # either is the mistake that was reported live -- "why did it buy war
    # paint, it should have bought horn cleat, removal and then cards" -- so
    # this is the user's stated order, relic then removal then cards.
    relics = _buyable(items, "relic")
    if relics:
        # Best relic, not cheapest; price only breaks ties. `relic_value`
        # scores by effect, weighting recurring effects over "Upon pickup".
        best = max(relics, key=lambda it: (relic_value(it), -_price(it)))
        return "shop_purchase", {"index": best["index"]}

    # 2. Card removal -- only worth it if there's actual junk to remove...
    # ...unless we are building Grand Finale ("can only be played if there are
    # no cards in your draw pile"), where *every* other card is junk and thinning
    # the deck is the entire plan. A live run held Grand Finale+ in a 20-card
    # deck, which can never fire.
    removals = _buyable(items, "card_removal")
    building_finale = card_db.holds_grand_finale(deck_names)
    if removals and (building_finale or any(n in JUNK_CARDS for n in deck_names)):
        return "shop_purchase", {"index": min(removals, key=_price)["index"]}

    # 3. Cards worth buying for this deck, keeping a reserve.
    #
    # First: if an unaffordable relic is within reach, stop buying. Every
    # cheap card bought now widens the gap and gold never comes back.
    unaffordable_relics = [
        it for it in items
        if (it.get("category") or "").lower() == "relic"
        and it.get("is_stocked", True)
        and not it.get("can_afford", False)
    ]
    if unaffordable_relics:
        nearest = min(_price(it) for it in unaffordable_relics)
        if 0 < nearest - gs.gold <= RELIC_SAVING_GAP:
            return "proceed", {}

    cards = _buyable(items, "card")
    if cards and not building_finale:
        counts = card_db.deck_tag_counts(deck_names)
        scored = sorted(cards, key=lambda it: card_db.score_card(_card_name(it), counts), reverse=True)
        best = scored[0]
        if (
            card_db.score_card(_card_name(best), counts) >= MIN_CARD_SCORE_TO_BUY
            and gs.gold - _price(best) >= CARD_PURCHASE_GOLD_RESERVE
        ):
            return "shop_purchase", {"index": best["index"]}

    # 4. Potions, only with a free slot.
    if len(gs.potions) < gs.max_potion_slots:
        potions = _buyable(items, "potion")
        if potions:
            return "shop_purchase", {"index": min(potions, key=_price)["index"]}

    return "proceed", {}
