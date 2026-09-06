"""What the act's boss is, and what that implies for deck building.

The map payload names the boss from the moment the act opens:

    "boss": {"col": 3, "row": 16, "id": "SOUL_FYSH_BOSS", "name": "Soul Fysh"}

Nothing used it. Every card-reward decision was made blind to the fight the
whole act is building toward, even though that fight is where runs actually
end -- floor 17 accounted for 11 of 20 deaths in one set.

The boss is remembered from the last map screen seen and cleared when the act
changes, so card rewards, shops and rest sites can all consult it.
"""
from __future__ import annotations

from typing import Any, Optional

# name -> (archetype to favour, extra weight for Block)
#
# **Lagavulin Matriarch** is the case this exists for: 222 HP behind "At the
# end of your turn, gain 12 Block", hitting for 23 with +4 Strength. Chip
# damage is absorbed outright, so a shiv or discard deck simply cannot get
# through -- but Poison ignores Block entirely, and heavy Block survives the
# 23s while the poison ticks. It is the single biggest killer of the current
# era: 6 of the last 40 runs.
BOSS_PLANS: dict[str, dict[str, Any]] = {
    "Lagavulin Matriarch": {"archetype": "poison", "block_bonus": 8.0},
}

_state: dict[str, Any] = {"act": None, "boss": None}


def note_raw(raw: dict[str, Any]) -> None:
    """Record the act's boss whenever a map screen goes past.

    Takes the raw payload rather than a GameState: the loop sees `raw` before
    it builds one, and this needs to run on every cycle. Only map screens
    carry the field, so the rest are no-ops.
    """
    act = ((raw or {}).get("run") or {}).get("act")
    if act != _state["act"]:
        _state["act"] = act
        _state["boss"] = None
    boss = ((raw or {}).get("map") or {}).get("boss") or {}
    name = boss.get("name")
    if name:
        _state["boss"] = name


def current_boss() -> Optional[str]:
    return _state["boss"]


def plan() -> dict[str, Any]:
    """The deck-building plan for the boss we are heading toward, if any."""
    return BOSS_PLANS.get(_state["boss"] or "", {})


def preferred_archetype() -> Optional[str]:
    return plan().get("archetype")


def block_bonus() -> float:
    """Extra score for Block on a card, when the boss demands surviving it."""
    return float(plan().get("block_bonus", 0.0))


def reset() -> None:
    _state["act"] = None
    _state["boss"] = None
