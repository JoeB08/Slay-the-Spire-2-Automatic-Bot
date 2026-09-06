"""Rest site screen.

NOTE: option shape not yet verified against a live fixture -- see README
"Known verification gaps". Matches by keyword against `type`/`name`/`title`
so it degrades gracefully if the exact field name differs.

Policy: **upgrade by default, heal when actually hurt.**

A 10-run sample chose Rest 22 times and Smith 0 times. The old rule rested
whenever the heal cleared a small absolute threshold, and since runs average
~98 damage taken, that threshold was essentially always met -- so the bot
never upgraded anything, all run. Healing is also a one-off while an upgrade
is permanent, and the sampled runs were dying to elites that out-scale a
20 HP top-up.

So the order is now: heal only when genuinely low (or when the upgrade would
be wasted), and otherwise take the upgrade.
"""
from __future__ import annotations

import re
from typing import Any

from .. import deck_memory
from ..game_state import GameState
from . import cards as card_db

# Rest sites heal a fraction of max HP when the amount isn't stated.
DEFAULT_REST_HEAL_FRACTION = 0.3
# Below this much real HP gained, an upgrade is the better use of the site.
MIN_WORTHWHILE_HEAL_FRACTION = 0.12
MIN_WORTHWHILE_HEAL_ABS = 8
# At or below this fraction of max HP, healing takes priority over upgrading:
# a permanent improvement is worth nothing if the next elite ends the run.
# Rest only when genuinely low. A 10-run sample rested 13 times and
# upgraded 7, while deck quality was the strongest predictor of how deep a
# run got (real cards vs floor: +0.77) -- so the balance shifts to Smith.
HEAL_PRIORITY_HP_FRACTION = 0.4
# A Smith is only worth taking if something in the deck actually gains from
# it. Calibrated against real imported values: a modest upgrade like Deadly
# Poison (5 -> 7 Poison) scores 2.0, so anything at or above that counts,
# while a deck of pure junk falls below.
MIN_UPGRADE_VALUE = 2.0

_HEAL_AMOUNT_RE = re.compile(r"(?:heal|restore|recover)\s+(\d+)", re.IGNORECASE)
_HEAL_PCT_RE = re.compile(r"(\d+)\s*%", re.IGNORECASE)


def _option_text(option: dict[str, Any]) -> str:
    return " ".join(
        str(option.get(key) or "")
        for key in ("type", "name", "title", "description")
    )


def _option_kind(option: dict[str, Any]) -> str:
    text = (option.get("type") or option.get("name") or option.get("title") or "").lower()
    if "rest" in text or "heal" in text or "sleep" in text:
        return "rest"
    if "smith" in text or "upgrade" in text:
        return "upgrade"
    if "dig" in text:
        return "dig"
    if "lift" in text:
        return "lift"
    if "toke" in text or "purge" in text or "remove" in text:
        return "remove"
    return text or "other"


def rest_hp_gain(option: dict[str, Any], gs: GameState) -> int:
    """HP this rest would actually restore, capped by what we're missing.

    The cap is the whole point: at 66/70 a 'heal 30%' rest returns 4, not 21,
    and spending the site on that instead of a permanent upgrade is a clear
    loss.
    """
    text = _option_text(option)
    missing = max(0, gs.max_hp - gs.hp)

    m = _HEAL_AMOUNT_RE.search(text)
    if m:
        amount = int(m.group(1))
    else:
        pct = _HEAL_PCT_RE.search(text)
        fraction = int(pct.group(1)) / 100 if pct else DEFAULT_REST_HEAL_FRACTION
        amount = int(round(gs.max_hp * fraction))

    return min(amount, missing)


def _heal_is_worthwhile(gain: int, gs: GameState) -> bool:
    threshold = max(MIN_WORTHWHILE_HEAL_ABS, MIN_WORTHWHILE_HEAL_FRACTION * gs.max_hp)
    return gain >= threshold


def _has_removable_junk(gs: GameState) -> bool:
    """Whether the deck holds anything worth deleting (starters or curses)."""
    deck = deck_memory.current_deck(gs)
    return any(card_db.removal_priority({"name": n}, deck) >= 100 for n in deck)


def _has_upgradable_card(gs: GameState) -> bool:
    """Whether anything in the deck would meaningfully benefit from upgrading.

    Uses the per-card upgrade values imported from the game rather than "is
    there any card at all" -- a Smith spent on a deck of starters returns
    almost nothing.
    """
    deck_names = deck_memory.current_deck(gs)
    if not deck_names:
        return False
    return max((card_db.upgrade_value(n, deck_names) for n in set(deck_names)), default=0.0) >= MIN_UPGRADE_VALUE


def decide_rest(gs: GameState) -> tuple[str, dict[str, Any]]:
    options = [o for o in (gs.rest_site.get("options") or []) if o.get("is_enabled", True)]
    if not options:
        return "proceed", {}

    kinds = {_option_kind(o): o for o in options}
    rest_option = kinds.get("rest")
    upgrade_option = kinds.get("upgrade")
    gain = rest_hp_gain(rest_option, gs) if rest_option else 0
    can_upgrade = upgrade_option is not None and _has_upgradable_card(gs)

    # Relics add extra options (Toke/purge, Dig, Lift...). Removing a card is
    # the strongest thing a rest site can offer a starter-heavy Silent deck --
    # better than a heal or a single upgrade -- and it was previously ignored
    # entirely because the decision only ever compared rest vs upgrade.
    remove_option = kinds.get("remove")
    if remove_option is not None and _has_removable_junk(gs):
        return "choose_rest_option", {"index": remove_option["index"]}

    # Hurt enough that the next fight is the real threat -- heal, provided the
    # heal is actually worth something.
    if rest_option and gs.hp_pct <= HEAL_PRIORITY_HP_FRACTION and _heal_is_worthwhile(gain, gs):
        return "choose_rest_option", {"index": rest_option["index"]}

    # Otherwise upgrade: permanent, and it compounds over the rest of the run.
    if can_upgrade:
        return "choose_rest_option", {"index": upgrade_option["index"]}

    # No upgrade available (or nothing worth upgrading) -- rest if it helps.
    if rest_option and gain > 0:
        return "choose_rest_option", {"index": rest_option["index"]}

    return "choose_rest_option", {"index": options[0]["index"]}
