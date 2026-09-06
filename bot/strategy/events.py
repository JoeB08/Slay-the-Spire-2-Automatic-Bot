"""Event ('?') screens, including Neow/Ancient at the start of a run.

Full event-by-event modeling (dozens of named events, each with its own best
answer) is out of scope -- this stays a text-heuristic policy over the option
descriptions the API gives us.

The key rule is HP-awareness: an HP cost that's a fine trade at full health
can end the run at 12/70. Penalties for losing HP therefore scale with how
much of our *current* HP the cost represents, and an option that would kill
us (or leave us critically low) is rejected outright rather than merely
down-weighted.
"""
from __future__ import annotations

import re
from typing import Any

from .. import deck_memory, explore
from ..game_state import GameState
from . import cards as card_db

_POSITIVE_HINTS = ("relic", "gold", "card", "potion", "gain", "upgrade")
_NEGATIVE_HINTS = ("curse", "lose", "damage")

# Deck-shaping options are worth far more than the generic keyword bonuses can
# express, but *how* much depends on the deck we actually have -- which is the
# whole point of note "chooses transform over upgrade every time".
_REMOVE_RE = re.compile(r"remove (\d+|a|an|one)\s+cards?", re.IGNORECASE)
_TRANSFORM_RE = re.compile(r"transform", re.IGNORECASE)
_UPGRADE_RE = re.compile(r"upgrade", re.IGNORECASE)

REMOVE_BONUS_PER_CARD = 6.0  # thinning is the strongest deck lever available
TRANSFORM_BONUS = 5.0  # good when the deck is mostly junk to reroll
UPGRADE_BONUS = 5.0  # good when we own cards actually worth improving
# A deck needs this share of genuinely good cards before upgrading beats
# transforming -- upgrading a starter Strike is close to worthless.
GOOD_CARD_SHARE_FOR_UPGRADE = 0.35

# "Lose 7 HP", "Take 5 damage", "Lose 10 Max HP"
_HP_COST_RE = re.compile(r"(?:lose|take|pay)\s+(\d+)\s*(?:hp|health|damage|max hp)", re.IGNORECASE)
_HP_PCT_COST_RE = re.compile(r"(?:lose|take|pay)\s+(\d+)\s*%\s*(?:of\s+)?(?:your\s+)?(?:max\s+)?(?:hp|health)", re.IGNORECASE)
_HEAL_RE = re.compile(r"heal\s+(\d+)|gain\s+(\d+)\s*(?:max\s+)?hp", re.IGNORECASE)
_GOLD_RE = re.compile(r"gain\s+(\d+)\s*gold", re.IGNORECASE)

# A heal that restores nothing is worse than neutral: these options usually
# carry a cost (a fight, a lost alternative).
WASTED_HEAL_PENALTY = -3.0
HEAL_VALUE_PER_HP = 0.25
# Gold is real but not decisive -- capped so a big pile can't outweigh safety.
GOLD_VALUE_PER_COIN = 0.05
GOLD_BONUS_CAP = 5.0


def _heal_amount(text: str) -> int:
    m = _HEAL_RE.search(text or "")
    if not m:
        return 0
    return int(next(g for g in m.groups() if g))


def _gold_amount(text: str) -> int:
    m = _GOLD_RE.search(text or "")
    return int(m.group(1)) if m else 0

# Below this fraction of max HP we're one bad fight from dying, so HP costs
# get treated as near-disqualifying rather than just expensive.
LOW_HP_FRACTION = 0.4
# Never voluntarily drop to or below this fraction of max HP for an event.
CRITICAL_HP_FRACTION = 0.2
REJECT = -1000.0


def _hp_cost(text: str, max_hp: int) -> int:
    """Best-effort HP cost named in an option's text (0 if none)."""
    cost = 0
    m = _HP_COST_RE.search(text)
    if m:
        cost = max(cost, int(m.group(1)))
    m = _HP_PCT_COST_RE.search(text)
    if m:
        cost = max(cost, int(round(max_hp * int(m.group(1)) / 100)))
    return cost


def _heals(text: str) -> bool:
    return bool(_HEAL_RE.search(text))


# Curse and Status names seen in live payloads (`type: Curse` / `type: Status`).
# Event prose names them without ever saying "curse", so the generic keyword
# list missed them entirely: "Choose 1 of 3 Rare cards to add to your Deck.
# Add 1 Injury to your Deck." scored the Injury at exactly nothing. Extracted
# from recorded runs rather than guessed, and safe to extend as new ones show
# up -- an unrecognised name simply costs nothing, which is today's behaviour.
KNOWN_CURSES = frozenset({
    "Decay", "Doubt", "Folly", "Greed", "Guilty", "Injury", "Normality",
    "Poor Sleep", "Spore Mind",
})
KNOWN_STATUSES = frozenset({"Dazed", "Infection", "Slimed", "Toxic", "Wound"})

# What one permanently-added junk card costs. Deliberately steep: it dilutes
# every future draw for the whole run, which is the same reason removal is the
# strongest deck lever (REMOVE_BONUS_PER_CARD = 6.0).
CURSE_COST = 7.0
STATUS_COST = 5.0

# "Add 1 Injury to your Deck", "Add 2 Wounds into your Deck"
_ADD_CARD_RE = re.compile(
    r"add\s+(\d+|a|an|one)\s+([A-Za-z][A-Za-z '-]*?)s?\s+(?:to|into)\s+your\s+deck",
    re.IGNORECASE,
)
# "Choose 1 of 3 Rare cards to add to your Deck"
_CHOOSE_RARITY_RE = re.compile(
    r"choose\s+\d+\s+of\s+(\d+)\s+(rare|uncommon|common)\s+cards?", re.IGNORECASE
)
RARITY_VALUE = {"rare": 9.0, "uncommon": 5.0, "common": 2.5}


def _added_junk_cost(text: str) -> float:
    """Cost of curses/statuses an option permanently adds to the deck."""
    total = 0.0
    for count_raw, name in _ADD_CARD_RE.findall(text):
        count = 1 if count_raw.lower() in ("a", "an", "one") else int(count_raw)
        card = name.strip().title()
        if card in KNOWN_CURSES:
            total += CURSE_COST * count
        elif card in KNOWN_STATUSES:
            total += STATUS_COST * count
    return total


def _card_offer_bonus(text: str) -> float:
    """Value of "choose 1 of N <rarity> cards".

    The generic hint list scored this at +1.0 for containing the word "card",
    so three Rare cards -- one of the strongest openings in the game -- tied
    with any sentence that happened to mention a card. A wider choice is worth
    a little more, but rarity dominates.
    """
    m = _CHOOSE_RARITY_RE.search(text)
    if not m:
        return 0.0
    choices, rarity = int(m.group(1)), m.group(2).lower()
    return RARITY_VALUE.get(rarity, 2.5) + min(choices, 5) * 0.5


def _deck_shaping_bonus(text: str, deck: list[str]) -> float:
    """Value of remove/transform/upgrade offers, given the deck we actually own.

    Removal scales with how much junk we're carrying. Transform and Upgrade
    pull in opposite directions: transforming rerolls a bad card (good when
    the deck is mostly starters), while upgrading improves a card we already
    like (worthless on a starter Strike). Treating them as interchangeable is
    why the bot took Transform every single time.
    """
    if not deck:
        return 0.0

    junk = sum(1 for n in deck if card_db.removal_priority({"name": n}, deck) >= 100)
    good = sum(1 for n in deck if (card_db._base_quality(n) or 0) >= 40)
    good_share = good / len(deck)
    bonus = 0.0

    m = _REMOVE_RE.search(text)
    if m and junk:
        raw = m.group(1).lower()
        count = 1 if raw in ("a", "an", "one") else int(raw)
        bonus += REMOVE_BONUS_PER_CARD * min(count, junk)

    if _TRANSFORM_RE.search(text) and junk:
        bonus += TRANSFORM_BONUS

    if _UPGRADE_RE.search(text) and good_share >= GOOD_CARD_SHARE_FOR_UPGRADE:
        bonus += UPGRADE_BONUS

    return bonus


def _score_option(
    option: dict[str, Any], hp: int, max_hp: int, deck: list[str] | None = None
) -> float:
    if option.get("is_locked"):
        return REJECT
    if option.get("is_proceed"):
        return -1.0  # only chosen when it's the only real option

    text = f"{option.get('title', '')} {option.get('description', '')}"
    lowered = text.lower()
    hp_fraction = hp / max(max_hp, 1)
    score = _deck_shaping_bonus(text, deck or [])
    # A card offer is worth what its rarity is worth, and a curse bolted onto
    # the same option is a real price -- neither was priced before.
    score += _card_offer_bonus(text)
    score -= _added_junk_cost(text)

    if option.get("relic_name"):
        score += 3.0
    for hint in _POSITIVE_HINTS:
        if hint in lowered:
            score += 1.0
    for hint in _NEGATIVE_HINTS:
        if hint in lowered:
            score -= 1.5

    cost = _hp_cost(text, max_hp)
    if cost:
        # Refuse anything that would kill us or leave us critically low.
        if hp - cost <= max(1, CRITICAL_HP_FRACTION * max_hp):
            return REJECT
        # Otherwise price it by the share of *current* HP it burns, escalating
        # hard once we're already hurt.
        share_of_current = cost / max(hp, 1)
        score -= 12.0 * share_of_current
        if hp_fraction < LOW_HP_FRACTION:
            score -= 8.0 * share_of_current

    # Healing is worth only what it actually restores. At 70/70 a "Heal 21 HP"
    # option returns nothing, so it must not read as a free positive -- the
    # bot took exactly that over 66 gold at full health, and paid for it with
    # a fight on top.
    heal = _heal_amount(text)
    if heal:
        realised = min(heal, max(0, max_hp - hp))
        if realised <= 0:
            score += WASTED_HEAL_PENALTY
        else:
            score += HEAL_VALUE_PER_HP * realised * (1.0 if hp_fraction < LOW_HP_FRACTION else 0.5)

    # Reward size matters: 66 gold and 5 gold previously scored identically,
    # which is how a big payout lost to a worthless heal.
    gold = _gold_amount(text)
    if gold:
        score += min(GOLD_BONUS_CAP, gold * GOLD_VALUE_PER_COIN)

    return score


def decide_event(gs: GameState) -> tuple[str, dict[str, Any]]:
    event = gs.event
    if event.get("in_dialogue"):
        return "advance_dialogue", {}

    options = event.get("options") or []
    if not options:
        return "proceed", {}

    hp, max_hp = gs.hp, gs.max_hp
    deck = deck_memory.current_deck(gs)

    # Relic exploration: take the least-sampled relic on offer instead of the
    # best-scoring one, so under-tested relics accumulate runs. Opt-in, and
    # these runs are tagged so they never pollute a performance comparison.
    if explore.enabled():
        least = explore.pick_least_sampled(
            options, lambda o: _score_option(o, hp, max_hp, deck)
        )
        if least is not None:
            return "choose_event_option", {"index": least["index"]}

    scored = sorted(options, key=lambda o: _score_option(o, hp, max_hp, deck), reverse=True)
    best = scored[0]

    # Everything on offer is disqualifying (all would drop us critically low).
    # Prefer bailing out via a proceed/leave option if the event provides one.
    if _score_option(best, hp, max_hp, deck) <= REJECT:
        for option in options:
            if option.get("is_proceed"):
                return "choose_event_option", {"index": option["index"]}
        # No way out -- take the cheapest HP cost rather than stalling.
        cheapest = min(
            options,
            key=lambda o: _hp_cost(f"{o.get('title', '')} {o.get('description', '')}", max_hp),
        )
        return "choose_event_option", {"index": cheapest["index"]}

    return "choose_event_option", {"index": best["index"]}
