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

from . import act_variant, explore, recording, relic_stats
from .game_state import GameState

RUNS_FILE = "runs.jsonl"

# The act bosses, as global floor numbers. These are the floors where every
# run that is still alive has had the same number of floors to shape a deck,
# which is what makes a deck measured here comparable between runs -- unlike
# the deck at death, which a run that died on floor 8 never got to build.
MILESTONE_FLOORS = (17, 33, 50)


class RunRecorder:
    # Dying happens inside a fight, so these are the only screens a run can
    # end on by death. Everything else means the player walked away alive.
    COMBAT_SCREENS = ("monster", "elite", "boss")

    def __init__(self, log_dir: Path, decision_log_name: str, profile_reader=None):
        # Callable returning the save profile, or None when no game is
        # reachable. Injected rather than built here so the recorder stays
        # testable offline and a profile read can never break a run.
        self.profile_reader = profile_reader
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
        # The game_over state reports hp 0 and carries no victory flag, so it
        # cannot tell a win from a death -- see finish(). These can.
        self.last_live_screen: Optional[str] = None
        self.last_live_hp: Optional[int] = None
        self.cleared_boss_floor: Optional[int] = None
        self.wins_at_start: Optional[int] = None
        self.recovery_events = 0
        # The card piles are only populated during combat, and are empty by
        # the time we reach game_over -- so snapshotting the deck at the end
        # records nothing. Keep the last real view we saw instead.
        self.last_known_deck: list[str] = []
        # Deck as read before any card was played -- see GameState.true_deck_names.
        self.true_deck: list[str] = []
        # The deck as it stood on each act-boss floor, keyed "a1f17".
        self.deck_at_floor: dict[str, list[str]] = {}
        self.last_known_relics: list[str] = []
        # Which act each relic was acquired in. The act-start relic choices
        # (Neow in act 1, Tezcatara in act 2, ...) are separate decisions with
        # different option pools, so pooling them would blur the signal.
        self.relic_act: dict[str, int] = {}
        # Every enemy met in Act 1, used to tag which Act 1 the run
        # rolled. Winning unlocked a second one with a different pool,
        # and the two overlap heavily -- see `act_variant`.
        self.act1_enemies: set[str] = set()

    def _note_milestone_deck(self, gs: GameState, deck: list[str]) -> None:
        """Keep the deck the run took into an act boss.

        First clean reading on the floor wins: later polls in the same fight
        can still be clean once the discard pile empties, and by then the deck
        may have changed. A run that *loses* the boss never reaches
        `game_over` holding this deck, which is the whole reason it has to be
        captured on the way past.
        """
        if gs.floor not in MILESTONE_FLOORS:
            return
        self.deck_at_floor.setdefault("a%sf%s" % (gs.act, gs.floor), sorted(deck))

    def _bump(self, key: str, amount: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount

    def _start_run(self, gs: GameState) -> None:
        self._reset()
        self.run_id = uuid.uuid4().hex[:12]
        self.started_ts = time.time()
        self.character = gs.player.get("character")
        self.last_hp = gs.hp
        self.hp_low_water = gs.hp
        # Read once here so finish() compares like with like. A profile that
        # cannot be read at either end leaves this None, and the run is
        # recorded as unconfirmed rather than as a loss.
        self.wins_at_start = self._wins()

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
            self.last_live_screen = gs.state_type
            self.last_live_hp = gs.hp

            self.decisions += 1
            self._bump(f"screen:{gs.state_type}")
            self._bump(f"action:{action}")

            # Prefer the pre-play reading: it excludes Shivs and other cards
            # the fight generated, which otherwise inflate the recorded deck.
            deck = gs.true_deck_names()
            if deck:
                self.true_deck = deck
                self._note_milestone_deck(gs, deck)
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
            # A rewards screen on the floor of the boss we were just fighting
            # is proof the boss died rather than us. Entering any new fight
            # clears it, so only the most recent boss can count.
            if gs.is_combat:
                self.cleared_boss_floor = None
            elif (gs.state_type == "rewards" and self.last_combat
                    and self.last_combat.get("state_type") == "boss"
                    and self.last_combat.get("floor") == floor):
                self.cleared_boss_floor = floor

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

    def _wins(self) -> Optional[int]:
        """Total runs won across the profile, or None if it cannot be read.

        Summed over every `times_won` anywhere in the payload rather than
        reached by a fixed path: only one run is ever in flight, so any
        increase is this run's, and a nesting change upstream cannot silently
        turn every win into a loss.
        """
        if self.profile_reader is None:
            return None
        try:
            payload = self.profile_reader()
        except Exception:
            return None

        total = 0
        found = False

        def walk(node):
            nonlocal total, found
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "times_won" and isinstance(value, bool) is False                             and isinstance(value, int):
                        total += value
                        found = True
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return total if found else None

    def note_event(self, event: str) -> None:
        if event in ("stuck_detected", "cycle_detected", "stale_action_detected"):
            self.recovery_events += 1
            self._bump(f"event:{event}")

    # --- completion ----------------------------------------------------

    def finish(self, gs: GameState) -> None:
        if self.run_id is None:
            return
        if not recording.enabled():
            # Nothing is written down in a copy without the RECORD_RUNS marker.
            # The run still ended, and the next one still starts clean.
            self._reset()
            return

        # gs.hp is 0 at game_over whether the run was won or lost, so it can
        # never be the test. The first 290 runs recorded were all written down
        # as deaths for exactly that reason -- including the victory on
        # 2026-11-19, which finished the act-3 boss with 2 HP left and was
        # filed as "killed by Aeonglass".
        #
        # What separates them is the last screen seen while the run was still
        # live: a death lands mid-fight, while a win leaves the final boss
        # through its rewards screen and the epilogue. A run that ends from
        # neither (abandoned, or a shape not seen before) is "ended" rather
        # than guessed at in either direction.
        # Only the game's own counter can call a win. Clearing the final boss
        # is NOT enough: the run then goes to The Architect, which can kill
        # you, and a death there leaves a trace identical to the victory on
        # 2026-11-19 -- boss cleared, rewards taken, event reached, HP still
        # showing, then a game_over that reports hp 0 either way. Inference
        # can separate a death from a non-death; it cannot separate a win
        # from dying at the last step, so it is not allowed to try.
        wins_now = self._wins()
        died = self.last_live_screen in self.COMBAT_SCREENS
        if (self.wins_at_start is not None and wins_now is not None
                and wins_now > self.wins_at_start):
            outcome, outcome_source = "victory", "profile"
        elif died:
            outcome, outcome_source = "death", "screens"
        else:
            # Survived the fighting but unconfirmed as a win. Undercounting a
            # win is recoverable; inventing one quietly corrupts the metric.
            outcome, outcome_source = "ended", "screens"
        hp = 0 if died else (self.last_live_hp or 0)
        record = {
            "run_id": self.run_id,
            "started_ts": self.started_ts,
            "ended_ts": time.time(),
            "duration_s": round(time.time() - (self.started_ts or time.time()), 1),
            "character": self.character,
            "outcome": outcome,
            "outcome_source": outcome_source,
            "cleared_final_boss": (self.cleared_boss_floor is not None
                                   and self.cleared_boss_floor == self.max_floor),
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
            "killed_by": self.last_combat if outcome == "death" else None,
            "deck": sorted(self.true_deck or gs.full_deck_names() or self.last_known_deck),
            "deck_size": len(self.true_deck or gs.full_deck_names() or self.last_known_deck),
            "deck_includes_tokens": not self.true_deck,
            "deck_at_floor": self.deck_at_floor,
            "relics": [r.get("name") for r in gs.relics] or self.last_known_relics,
            "potions": [p.get("name") for p in gs.potions],
            "counts": self.counts,
            "decision_log": self.decision_log_name,
        }

        with open(self._runs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        # A readable account of the run for a person to go through -- see
        # `run_story`. It only reads what is already logged, and nothing it
        # does may stop the bot.
        try:
            from . import run_story

            run_story.write_story(
                self.log_dir / self.decision_log_name, record, self.log_dir / run_story.STORIES_DIR
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[bot] could not write the run story: {exc}")

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
