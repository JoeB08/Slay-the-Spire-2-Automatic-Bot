"""Lightweight wrapper over the raw STS2MCP state JSON.

The API is a fast-moving early-access mod, so this stays a thin, defensive
accessor layer over the raw dict rather than strict dataclasses -- unknown/
missing fields degrade to sensible defaults instead of raising.
"""
from __future__ import annotations

from typing import Any, Optional


class GameState:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    # --- top level ---
    @property
    def state_type(self) -> str:
        return self.raw.get("state_type", "")

    @property
    def is_combat(self) -> bool:
        return self.state_type in ("monster", "elite", "boss")

    @property
    def run(self) -> dict[str, Any]:
        return self.raw.get("run") or {}

    @property
    def act(self) -> int:
        return self.run.get("act", 1)

    @property
    def floor(self) -> int:
        return self.run.get("floor", 0)

    @property
    def ascension(self) -> int:
        return self.run.get("ascension", 0)

    # --- player ---
    @property
    def player(self) -> dict[str, Any]:
        return self.raw.get("player") or {}

    @property
    def hp(self) -> int:
        return self.player.get("hp", 0)

    @property
    def max_hp(self) -> int:
        return self.player.get("max_hp", 1)

    @property
    def hp_pct(self) -> float:
        return self.hp / max(self.max_hp, 1)

    @property
    def gold(self) -> int:
        return self.player.get("gold", 0)

    @property
    def energy(self) -> int:
        return self.player.get("energy", 0)

    @property
    def max_energy(self) -> int:
        return self.player.get("max_energy", 3)

    @property
    def relics(self) -> list[dict[str, Any]]:
        return self.player.get("relics") or []

    def has_relic(self, relic_id: str) -> bool:
        return any(r.get("id") == relic_id for r in self.relics)

    @property
    def potions(self) -> list[dict[str, Any]]:
        return self.player.get("potions") or []

    @property
    def max_potion_slots(self) -> int:
        return self.player.get("max_potion_slots", 3)

    @property
    def hand(self) -> list[dict[str, Any]]:
        return self.player.get("hand") or []

    @property
    def draw_pile(self) -> list[dict[str, Any]]:
        return self.player.get("draw_pile") or []

    @property
    def discard_pile(self) -> list[dict[str, Any]]:
        return self.player.get("discard_pile") or []

    @property
    def exhaust_pile(self) -> list[dict[str, Any]]:
        return self.player.get("exhaust_pile") or []

    def true_deck_names(self) -> list[str] | None:
        """The real deck, or None if this moment can't tell us.

        `full_deck_names` sums every pile, which mid-combat includes cards the
        fight generated -- one logged run reported a 42-card deck of which 20
        were Shivs. Before anything has been played, though, the discard and
        exhaust piles are empty and draw + hand is exactly the deck, tokens
        included only if a relic or Innate card put them there. That is the
        one instant in a run where the count matches what the game shows in
        the corner, so it is the only one trusted here.
        """
        player = self.raw.get("player") or {}
        if player.get("discard_pile_count") or player.get("exhaust_pile_count"):
            return None
        if player.get("discard_pile") or player.get("exhaust_pile"):
            return None
        # Status cards are put there by the *enemy*, not chosen: a Wriggler
        # pack shuffles Infection into the draw pile before the first play, so
        # they sit in this pre-play snapshot and inflate the count. One logged
        # 14-card deck read as 23 because nine of them were Infection. Curses
        # are kept -- an event curse really is part of the deck.
        cards = list(player.get("draw_pile") or []) + list(player.get("hand") or [])
        names = [
            c.get("name") for c in cards
            if c.get("name") and (c.get("type") or "").lower() != "status"
        ]
        return names or None

    def full_deck_names(self) -> list[str]:
        """Best-effort list of card names currently in the deck (hand+draw+discard+exhaust)."""
        names = []
        for pile in (self.hand, self.draw_pile, self.discard_pile, self.exhaust_pile):
            for c in pile:
                n = c.get("name")
                if n:
                    names.append(n)
        return names

    # --- combat ---
    @property
    def battle(self) -> dict[str, Any]:
        return self.raw.get("battle") or {}

    @property
    def enemies(self) -> list[dict[str, Any]]:
        return [e for e in (self.battle.get("enemies") or []) if e.get("hp", 0) > 0]

    @property
    def is_play_phase(self) -> bool:
        return bool(self.battle.get("is_play_phase", False))

    # --- screen-specific payloads ---
    @property
    def event(self) -> dict[str, Any]:
        return self.raw.get("event") or {}

    @property
    def map(self) -> dict[str, Any]:
        return self.raw.get("map") or {}

    @property
    def rewards(self) -> dict[str, Any]:
        return self.raw.get("rewards") or {}

    @property
    def card_reward(self) -> dict[str, Any]:
        return self.raw.get("card_reward") or {}

    @property
    def shop(self) -> dict[str, Any]:
        return self.raw.get("shop") or {}

    @property
    def rest_site(self) -> dict[str, Any]:
        return self.raw.get("rest_site") or {}

    @property
    def relic_select(self) -> dict[str, Any]:
        return self.raw.get("relic_select") or {}

    @property
    def treasure(self) -> dict[str, Any]:
        return self.raw.get("treasure") or {}

    @property
    def menu_screen(self) -> Optional[str]:
        return self.raw.get("menu_screen")

    @property
    def options(self) -> Any:
        return self.raw.get("options")

    def __repr__(self) -> str:
        return f"GameState({self.state_type}, act={self.act} floor={self.floor} hp={self.hp}/{self.max_hp})"
