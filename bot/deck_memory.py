"""Remembers the deck between combats.

`GameState.full_deck_names()` reads the hand/draw/discard/exhaust piles, and
those are **only populated during combat**. Everywhere else -- shops, rest
sites, card rewards -- the deck reads as empty.

That silently broke real decisions: `shop.py` gated card removal on "do I own
Strikes/Defends worth cutting?", the deck looked empty, so across 10 recorded
runs removal was offered 21 times, was affordable at least 6 times, and was
bought exactly 0 times. Every run ended with all 10 starters intact.

So: snapshot the deck whenever combat makes it visible, and serve that
snapshot to anything asking outside combat.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .game_state import GameState

# Persisted so a bot restart mid-run doesn't start blind -- see load().
CACHE_FILE = "deck_memory.json"

_deck: list[str] = []
_last_floor: Optional[int] = None


def reset() -> None:
    global _deck, _last_floor
    _deck = []
    _last_floor = None


def _cache_path() -> Path:
    return Path(__file__).parent.parent / "logs" / CACHE_FILE


def load() -> None:
    """Restore the deck remembered by a previous bot process.

    Restarting mid-run otherwise leaves the memory cold until the next
    combat, and every deck-dependent decision silently degrades in the
    meantime -- a campfire hit right after a restart saw an empty deck,
    concluded there was nothing worth upgrading, and healed instead.
    """
    global _deck, _last_floor
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data.get("deck"), list) and data["deck"]:
        _deck = [str(n) for n in data["deck"]]
        _last_floor = data.get("floor")


def _save() -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps({"deck": _deck, "floor": _last_floor}), encoding="utf-8"
        )
    except OSError:
        pass  # cache is an optimisation, never worth failing a run over


def remember(gs: GameState) -> None:
    """Call once per decision. Stores the deck when it's visible, and clears
    the memory when a new run starts (floor going backwards)."""
    global _deck, _last_floor

    floor = gs.floor
    if floor > 0:
        if _last_floor is not None and floor < _last_floor:
            _deck = []  # new run -- the old deck is not ours any more
            _save()
        _last_floor = floor

    # Prefer the pre-play reading. `full_deck_names` sums every pile, so
    # mid-combat it includes cards the *fight* made: one 20-run sample had
    # Infection x80 and Shiv x16 among 181 supposedly-chosen cards. Everything
    # deck-relative was reading that -- archetype detection, synergy counts,
    # removal copy-counts, and the card-intake bar, which is a median over the
    # deck and so moves directly with the junk.
    visible = gs.true_deck_names()
    if visible and visible != _deck:
        _deck = visible
        _save()


def current_deck(gs: GameState) -> list[str]:
    """The deck as best we know it: live piles when in combat, else the last
    snapshot. Callers outside combat must use this, never
    `gs.full_deck_names()` directly."""
    # Never hand back the contaminated live reading in preference to a clean
    # remembered one -- outside combat the piles are empty anyway, and inside
    # combat they are full of generated tokens.
    return list(_deck) or gs.true_deck_names() or gs.full_deck_names()
