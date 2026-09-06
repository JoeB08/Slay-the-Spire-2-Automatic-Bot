"""Boss relic choice screen.

No structured relic database (STS2's ~8 relics/character/act aren't captured
in silent_cards.json). Scores by keyword overlap with our established
archetype tags against the relic's own description text, falling back to
"always take a relic" since skipping is rarely correct even for a middling fit.
"""
from __future__ import annotations

from typing import Any

from ..game_state import GameState

_ARCHETYPE_KEYWORDS = (
    "poison", "shiv", "discard", "weak", "vulnerable", "block", "energy", "draw", "dexterity",
)


def _score_relic(relic: dict[str, Any]) -> float:
    text = f"{relic.get('name', '')} {relic.get('description', '')}".lower()
    return sum(1.0 for kw in _ARCHETYPE_KEYWORDS if kw in text)


def decide_relic_select(gs: GameState) -> tuple[str, dict[str, Any]]:
    options = gs.relic_select.get("relics") or gs.relic_select.get("options") or []
    if not options:
        return "skip_relic_selection", {}
    best = max(options, key=_score_relic)
    return "select_relic", {"index": best.get("index", 0)}
