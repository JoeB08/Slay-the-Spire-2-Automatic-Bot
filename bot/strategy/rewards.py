"""Post-combat rewards screen and the card_reward sub-screen it opens."""
from __future__ import annotations

from typing import Any

from .. import deck_memory
from ..game_state import GameState
from . import cards as card_db
from . import potions as potion_db

# Only swap out a held potion when the offered one is clearly better -- a
# marginal upgrade isn't worth spending an action and risking a worse roll.
POTION_SWAP_MARGIN = 10.0

# Floors where we opened the card reward, decided nothing was worth taking,
# and skipped. Claiming the card reward again just reopens the same offer, so
# without this the two screens ping-pong forever: claim -> skip -> claim ->
# skip (a live run did this 298 times). The alternating state defeats the
# loop's stale-action detector, which only catches a decision repeating
# against unchanged state.
# Keyed on (act, floor) alone this leaked across runs: once the bot skipped a
# card reward at act 1 floor 2, *every later run in the session* auto-skipped
# its floor-2 card without ever opening it. Five such cases were logged in one
# set, all at low floors that recur every run. The memory only needs to last
# for the current visit, so it is scoped to the run and cleared when a new one
# starts.
_skipped_card_rewards: set[tuple[int, int]] = set()
_skip_memory_run: object | None = None


def _forget_skips_on_new_run(gs: GameState) -> None:
    """Drop the skip memory when the floor goes backwards -- a new run."""
    global _skip_memory_run
    marker = (gs.act, gs.floor)
    if _skip_memory_run is not None:
        prev_act, prev_floor = _skip_memory_run
        if gs.act < prev_act or (gs.act == prev_act and gs.floor < prev_floor):
            _skipped_card_rewards.clear()
    _skip_memory_run = marker


def decide_rewards(gs: GameState) -> tuple[str, dict[str, Any]]:
    """Claim rewards one at a time, skipping any we can't actually take.

    A potion reward with a full potion belt is the important case: the API
    accepts the claim and returns "ok" while nothing happens, so naively
    always claiming items[0] spins forever (a live run burned 90 actions on
    exactly this). Nothing in the payload flags it -- we have to compare the
    player's potion count against max_potion_slots ourselves.
    """
    _forget_skips_on_new_run(gs)
    items = gs.rewards.get("items") or []
    potions_full = len(gs.potions) >= gs.max_potion_slots
    already_skipped_card = (gs.act, gs.floor) in _skipped_card_rewards

    def _claimable(item: dict[str, Any]) -> bool:
        kind = (item.get("type") or "").lower()
        if potions_full and kind == "potion":
            return False
        if already_skipped_card and kind == "card":
            return False  # reopening it would just re-offer what we passed on
        return True

    claimable = [it for it in items if _claimable(it)]
    if claimable:
        return "claim_reward", {"index": claimable[0]["index"]}

    # Belt is full, so the potion reward was filtered out above -- but if the
    # offered potion clearly beats our worst one, make room instead of walking
    # away from a straight upgrade. discard_potion works outside combat.
    if potions_full:
        offered = [it for it in items if (it.get("type") or "").lower() == "potion"]
        if offered:
            best_offered = max(offered, key=potion_db.potion_value)
            worst_held = min(gs.potions, key=potion_db.potion_value)
            if potion_db.potion_value(best_offered) > potion_db.potion_value(worst_held) + POTION_SWAP_MARGIN:
                return "discard_potion", {"slot": worst_held["slot"]}

    return "proceed", {}


def decide_card_reward(gs: GameState) -> tuple[str, dict[str, Any]]:
    cr = gs.card_reward
    offered = cr.get("cards") or []
    if not offered:
        return "skip_card_reward", {}
    idx = card_db.best_card_reward_index(
        offered, deck_memory.current_deck(gs), act=gs.act, floor=gs.floor,
        relics=gs.relics,
    )
    if idx is None:
        if cr.get("can_skip", True):
            _skipped_card_rewards.add((gs.act, gs.floor))
            return "skip_card_reward", {}
        idx = offered[0]["index"]
    return "select_card_reward", {"card_index": idx}
