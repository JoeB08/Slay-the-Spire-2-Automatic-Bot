"""Per-run recording, for comparing many runs after the fact.

The per-decision logs (`logs/run_<timestamp>.jsonl`) are written per bot
*process*, which is the wrong unit for "how is the bot actually doing?" -- one
process can play several runs, and one run can span processes after a restart.

This module tracks the boundaries of an actual game run and appends one
summary line per finished run to `logs/runs.jsonl`. That file is the artifact
to analyse across N runs: outcome, how far it got, what it was carrying, what
killed it, and counts of the decisions that shaped the run.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import act_variant, explore, relic_stats
from .game_state import GameState

RUNS_FILE = "runs.jsonl"


class RunRecorder:
    def __init__(self, log_dir: Path, decision_log_name: str):
        self.log_dir = log_dir
        self.decision_log_name = decision_log_name
        self._runs_path = log_dir / RUNS_FILE
        self._reset()

    # --- lifecycle -----------------------------------------------------

    def _reset(self) -> None:
        self.run_id: Optional[str] = None
        self.started_ts: Optional[float] = None
        self.character: Optional[str] = None
        self.max_act = 0
        self.max_floor = 0
        self.last_floor: Optional[int] = None
        self.last_hp: Optional[int] = None
        self.hp_low_water: Optional[int] = None
        self.max_gold = 0
        self.damage_taken = 0
        self.decisions = 0
        self.counts: dict[str, int] = {}
        self.last_combat: Optional[dict[str, Any]] = None
        self.recovery_events = 0
        # The card piles are only populated during combat, and are empty by
        # the time we reach game_over -- so snapshotting the deck at the end
        # records nothing. Keep the last real view we saw instead.
        self.last_known_deck: list[str] = []
        # Deck as read before any card was played -- see GameState.true_deck_names.
        self.true_deck: list[str] = []
        self.last_known_relics: list[str] = []
        # Which act each relic was acquired in. The act-start relic choices
        # (Neow in act 1, Tezcatara in act 2, ...) are separate decisions with
        # different option pools, so pooling them would blur the signal.
        self.relic_act: dict[str, int] = {}
        # Every enemy met in Act 1, used to tag which Act 1 the run
        # rolled. Winning unlocked a second one with a different pool,
        # and the two overlap heavily -- see `act_variant`.
        self.act1_enemies: set[str] = set()

    def _bump(self, key: str, amount: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount

    def _start_run(self, gs: GameState) -> None:
        self._reset()
        self.run_id = uuid.uuid4().hex[:12]
        self.started_ts = time.time()
        self.character = gs.player.get("character")
        self.last_hp = gs.hp
        self.hp_low_water = gs.hp

    # --- observation ---------------------------------------------------

    def observe(self, gs: GameState, action: str, fields: dict[str, Any]) -> None:
        """Called for every decision the bot actually acts on."""
        floor = gs.floor

        # A run starts when we first see a real floor, and restarts whenever
        # the floor goes backwards (new run began).
        in_run = gs.state_type not in ("menu", "game_over") and floor > 0
        if in_run:
            if self.run_id is None or (self.last_floor is not None and floor < self.last_floor):
                self._start_run(gs)
            self.last_floor = floor
            self.max_act = max(self.max_act, gs.act)
            self.max_floor = max(self.max_floor, floor)
            self.max_gold = max(self.max_gold, gs.gold)

            if self.last_hp is not None and gs.hp < self.last_hp:
                self.damage_taken += self.last_hp - gs.hp
            self.last_hp = gs.hp
            self.hp_low_water = gs.hp if self.hp_low_water is None else min(self.hp_low_water, gs.hp)

            self.decisions += 1
            self._bump(f"screen:{gs.state_type}")
            self._bump(f"action:{action}")

            # Prefer the pre-play reading: it excludes Shivs and other cards
            # the fight generated, which otherwise inflate the recorded deck.
            deck = gs.true_deck_names()
            if deck:
                self.true_deck = deck
            fallback = gs.full_deck_names()
            if fallback:
                self.last_known_deck = fallback
            relics = [r.get("name") for r in gs.relics]
            if relics:
                self.last_known_relics = relics
                # Stamp each newly-seen relic with the act we picked it up in.
                for name in relics:
                    if name and name not in self.relic_act:
                        self.relic_act[name] = gs.act
            if gs.is_combat:
                if gs.act == 1:
                    for enemy in gs.enemies:
                        if enemy.get("name"):
                            self.act1_enemies.add(enemy["name"])
                self.last_combat = {
                    "state_type": gs.state_type,
                    "floor": floor,
                    "enemies": [
                        {"name": e.get("name"), "hp": e.get("hp"), "max_hp": e.get("max_hp")}
                        for e in gs.enemies
                    ],
                }

        if gs.state_type == "game_over" and self.run_id is not None:
            self.finish(gs)

    def note_event(self, event: str) -> None:
        if event in ("stuck_detected", "cycle_detected", "stale_action_detected"):
            self.recovery_events += 1
            self._bump(f"event:{event}")

    # --- completion ----------------------------------------------------

    def finish(self, gs: GameState) -> None:
        if self.run_id is None:
            return

        hp = gs.hp
        outcome = "death" if hp <= 0 else "ended"
        record = {
            "run_id": self.run_id,
            "started_ts": self.started_ts,
            "ended_ts": time.time(),
            "duration_s": round(time.time() - (self.started_ts or time.time()), 1),
            "character": self.character,
            "outcome": outcome,
            "act_reached": self.max_act,
            "floor_reached": self.max_floor,
            "ascension": gs.ascension,
            "neow_explore": explore.enabled(),
            "act1_variant": act_variant.classify(self.act1_enemies),
            "act1_enemies": sorted(self.act1_enemies),
            "final_hp": hp,
            "max_hp": gs.max_hp,
            "hp_low_water": self.hp_low_water,
            "gold": gs.gold,
            "max_gold": self.max_gold,
            "damage_taken": self.damage_taken,
            "decisions": self.decisions,
            "recovery_events": self.recovery_events,
            "killed_by": self.last_combat,
            "deck": sorted(self.true_deck or gs.full_deck_names() or self.last_known_deck),
            "deck_size": len(self.true_deck or gs.full_deck_names() or self.last_known_deck),
            "deck_includes_tokens": not self.true_deck,
            "relics": [r.get("name") for r in gs.relics] or self.last_known_relics,
            "potions": [p.get("name") for p in gs.potions],
            "counts": self.counts,
            "decision_log": self.decision_log_name,
        }

        with open(self._runs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        # Also append to the persistent relic history, which lives outside
        # logs/ so it survives the log wipes between evaluation sets -- relic
        # correlations only become meaningful once runs accumulate.
        relic_stats.record_run(
            relics=record["relics"],
            floor_reached=record["floor_reached"],
            act_reached=record["act_reached"],
            outcome=record["outcome"],
            run_id=record["run_id"],
            damage_taken=record["damage_taken"],
            relic_act={n: a for n, a in self.relic_act.items() if n in set(record["relics"])},
            ascension=record.get("ascension", 0),
            explore=record.get("neow_explore", False),
            act1_variant=record.get("act1_variant"),
        )

        self._reset()
