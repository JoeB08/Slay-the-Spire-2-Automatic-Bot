"""The autonomous play loop: poll state, dispatch by state_type, act, log.

Logs one JSON line per decision to logs/run_<timestamp>.jsonl, including the
state summary, the action taken, and (once available on the next poll) the
outcome delta -- HP/gold/floor change since the prior decision. That's not
just for debugging: if a self-improving version gets built on top of this
rules-based one later, these logs are the (state, action, outcome) traces it
would train from, so the schema is kept deliberately structured rather than
free-text.
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from . import deck_memory
from .api_client import ApiClient, ApiError
from .game_state import GameState
from .recovery import MAX_CONSECUTIVE_RECOVERIES, RELAUNCH_WAIT_ATTEMPTS, RELAUNCH_WAIT_DELAY, StuckDetector, kill_and_relaunch
from .run_recorder import RunRecorder
from .strategy import combat, events, map_nav, menu, misc_screens, relics, rest, rewards, shop
from .strategy import boss_intel

LOG_DIR = Path(__file__).parent.parent / "logs"
POLL_IDLE_SLEEP = 0.5
ERROR_BACKOFF = 1.5
ABANDON_AFTER_N_RECOVERIES = 2  # try resuming once; only give up on the run if it freezes again right after
POST_ACTION_DELAY = 0.3  # let the game finish processing an action's effect/animation before polling again
# Cards that draw, discard or shuffle rearrange the whole hand and animate
# per card, so they take noticeably longer to resolve than a plain attack.
# Both of the worst freezes seen live came from exactly these -- Calculated
# Gamble (discard hand, redraw) and Grand Finale -- so they get a longer
# settle before the next poll.
HAND_CHURN_DELAY = 1.2
# Sly cards resolve a beat after being discarded, so re-check before
# committing to end the turn.
END_TURN_CONFIRM_DELAY = 0.6
# The abandon_run confirmation popup is short-lived -- answer it promptly.
ABANDON_CONFIRM_ATTEMPTS = 6
ABANDON_CONFIRM_POLL = 0.3
_HAND_CHURN_RE = re.compile(r"\b(draw|discard|shuffle)\b", re.IGNORECASE)
STALE_ACTION_LIMIT = 4  # same action + unchanged decision state this many times in a row -> escape
# After a successful action, poll (cheaply) until the state actually reflects
# it before deciding again. Big swings need real time to resolve -- Grand
# Finale (60 damage to ALL enemies) ends the fight outright, but the mod kept
# serving the old combat state, and the bot spammed cards into a dead combat.
# Waiting costs a few local HTTP polls; not waiting costs wasted actions,
# bogus errors, and cards played into nothing.
SETTLE_MAX_POLLS = 12
SETTLE_POLL_DELAY = 0.25
# Cycle detection. STALE_ACTION_LIMIT only catches one decision repeating
# against unchanged state; it cannot see an A->B->A->B ping-pong, where the
# state genuinely changes each step but the pair makes no progress (live case:
# rewards claim_reward -> card_reward skip -> back to rewards, 298 times).
CYCLE_WINDOW = 8  # recent decisions to inspect
CYCLE_DISTINCT_MAX = 2  # this few distinct decisions filling the window == a cycle


def _summary(gs: GameState) -> dict[str, Any]:
    return {
        "state_type": gs.state_type,
        "act": gs.act,
        "floor": gs.floor,
        "hp": gs.hp,
        "max_hp": gs.max_hp,
        "gold": gs.gold,
    }


# Screens whose field shapes aren't yet confirmed against a live payload
# (README "Known verification gaps") -- log the raw section every time one
# comes up so the next real occurrence is diagnosable from the log alone,
# the way the hand_select/card_select/game_over bugs were found and fixed.
_UNVERIFIED_SCREENS = (
    "shop",
    "rest_site",
    "treasure",
    "bundle_select",
    "crystal_sphere",
    "relic_select",  # boss relic / forced choices -- shape never captured live yet
    "card_select",  # removal/transform prompts: the prompt text drives the pick
)


def _raw_for_replay(gs: GameState) -> Optional[dict[str, Any]]:
    """The full payload behind a combat decision, so it can be replayed.

    Combat used to log only a summary -- `hand` as strings like
    "Strike(1,can_play=True)" -- which is enough to *describe* a decision and
    not enough to *reproduce* it. A live "why did it keep that Shiv?" could not
    be answered: reconstructing the state by hand gave a different answer than
    the bot had given, with no way to tell which was wrong.

    It is also the difference between training data and prose, which matters
    for anything learned from these logs later.
    """
    if not gs.is_combat:
        return None
    return {"player": gs.raw.get("player"), "battle": gs.raw.get("battle"),
            "run": gs.raw.get("run"), "state_type": gs.raw.get("state_type")}


def _unverified_screen_detail(gs: GameState) -> Optional[dict[str, Any]]:
    if gs.state_type not in _UNVERIFIED_SCREENS:
        return None
    return gs.raw.get(gs.state_type)


def _settle_delay_for(gs: GameState, action: str, fields: dict[str, Any]) -> float:
    """How long to let an action resolve before polling again.

    Draw/discard/shuffle effects rewrite the hand and animate per card, so
    they need materially longer than a plain attack. Applies to potions too
    (Swift Potion draws 3).
    """
    if action not in ("play_card", "use_potion"):
        return POST_ACTION_DELAY

    if action == "play_card":
        index = fields.get("card_index")
        card = next((c for c in gs.hand if c.get("index") == index), None)
    else:
        slot = fields.get("slot")
        card = next((p for p in gs.potions if p.get("slot") == slot), None)

    text = (card or {}).get("description") or ""
    return HAND_CHURN_DELAY if _HAND_CHURN_RE.search(text) else POST_ACTION_DELAY


def _card_brief(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": card.get("name") or card.get("card_name"),
        "cost": card.get("cost"),
        "type": card.get("type") or card.get("card_type"),
        "can_play": card.get("can_play"),
        "why_not": card.get("unplayable_reason"),
        "text": card.get("description") or card.get("card_description"),
    }


def _available_options(gs: GameState) -> Optional[dict[str, Any]]:
    """Every choice on offer at this decision point -- not just the one taken.

    Analysis after N runs needs to ask "what did it pass up?", which the action
    alone can't answer: a bad card pick and a forced one look identical unless
    the alternatives were recorded. Captured for each decision screen:
    combat turns, events, shops, campfires, card rewards, map, and the
    selection overlays.
    """
    st = gs.state_type

    if gs.is_combat:
        return {
            "hand": [_card_brief(c) for c in gs.hand],
            "potions": [
                {"slot": p.get("slot"), "name": p.get("name"), "text": p.get("description")}
                for p in gs.potions
            ],
            "enemies": [
                {
                    "id": e.get("entity_id"),
                    "name": e.get("name"),
                    "hp": e.get("hp"),
                    "block": e.get("block"),
                    # Keep the *description*, not just name+amount. Unknown
                    # mechanics are exactly the ones worth logging, and the
                    # name alone says nothing: "Hardened Shell 20" and
                    # "Shriek 70" were unreadable from the logs until the
                    # payload was inspected live. The description is where the
                    # rule lives ("The first time its HP reaches 70 or below,
                    # it becomes Stunned").
                    "status": [
                        {
                            "name": s.get("name"),
                            "amount": s.get("amount"),
                            "type": s.get("type"),
                            "text": s.get("description"),
                        }
                        for s in e.get("status", [])
                    ],
                    "intents": [
                        {"type": i.get("type"), "label": i.get("label"), "text": i.get("description")}
                        for i in e.get("intents", [])
                    ],
                }
                for e in gs.enemies
            ],
            "player_status": [
                {"name": s.get("name"), "amount": s.get("amount")} for s in gs.player.get("status", [])
            ],
            "draw_remaining": gs.player.get("draw_pile_count"),
            "discard_count": gs.player.get("discard_pile_count"),
            # Names, not just counts: the deck is visible only during combat,
            # and without the contents there is no way to ask what the deck
            # looked like at a fixed point (e.g. the end of the first elite).
            "piles": {
                pile: [c.get("name") for c in (gs.player.get(pile) or [])]
                for pile in ("draw_pile", "discard_pile", "exhaust_pile")
            },
        }

    if st == "event":
        return {
            "event": gs.event.get("event_name") or gs.event.get("event_id"),
            "options": [
                {
                    "index": o.get("index"),
                    "title": o.get("title"),
                    "text": o.get("description"),
                    "relic": o.get("relic_name"),
                    "locked": o.get("is_locked"),
                    "is_proceed": o.get("is_proceed"),
                }
                for o in (gs.event.get("options") or [])
            ],
        }

    if st == "shop":
        return {
            "gold": gs.gold,
            "items": [
                {
                    "index": it.get("index"),
                    "category": it.get("category"),
                    "price": it.get("price"),
                    "stocked": it.get("is_stocked"),
                    "affordable": it.get("can_afford"),
                    "name": it.get("card_name") or it.get("relic_name") or it.get("potion_name"),
                    "text": it.get("card_description")
                    or it.get("relic_description")
                    or it.get("potion_description"),
                }
                for it in (gs.shop.get("items") or [])
            ],
        }

    if st == "rest_site":  # campfire
        return {
            "hp": gs.hp,
            "max_hp": gs.max_hp,
            "options": [
                {
                    "index": o.get("index"),
                    "type": o.get("type"),
                    "name": o.get("name") or o.get("title"),
                    "text": o.get("description"),
                }
                for o in (gs.rest_site.get("options") or [])
            ],
        }

    if st == "card_reward":
        cr = gs.card_reward
        return {
            "can_skip": cr.get("can_skip"),
            "cards": [
                {
                    "index": c.get("index"),
                    "name": c.get("name"),
                    "rarity": c.get("rarity"),
                    "type": c.get("type"),
                    "cost": c.get("cost"),
                    "text": c.get("description"),
                }
                for c in (cr.get("cards") or [])
            ],
        }

    if st == "rewards":
        return {
            "items": [
                {"index": i.get("index"), "type": i.get("type"), "text": i.get("description")}
                for i in (gs.rewards.get("items") or [])
            ]
        }

    if st == "map":
        return {
            "next_options": [
                {"index": o.get("index"), "type": o.get("type"), "col": o.get("col"), "row": o.get("row")}
                for o in (gs.map.get("next_options") or [])
            ]
        }

    if st in ("relic_select", "bundle_select", "card_select", "hand_select", "treasure"):
        section = gs.raw.get(st) or {}
        offered = section.get("cards") or section.get("relics") or section.get("bundles") or section.get("options") or []
        return {
            "prompt": section.get("prompt"),
            "screen_type": section.get("screen_type"),
            "can_confirm": section.get("can_confirm"),
            "can_cancel": section.get("can_cancel"),
            "offered": [
                {
                    "index": o.get("index"),
                    "name": o.get("name"),
                    "text": o.get("description"),
                    "cards": [c.get("name") for c in (o.get("cards") or [])] or None,
                }
                for o in offered
            ],
        }

    return None


def _combat_detail(gs: GameState) -> Optional[dict[str, Any]]:
    """Extra detail logged only during combat -- hand/energy at decision time.
    Cheap enough to always include, and it's what actually makes a stuck-loop
    or a wasted-resource bug (e.g. a free card left unplayed) diagnosable from
    the log alone instead of needing to catch the game live."""
    # `is_combat` is false on hand_select / card_select, so every forced
    # discard logged incoming/block/unblocked as None -- exactly the numbers
    # needed to judge whether pitching a Strike over a Defend was right.
    # Fall back to the presence of a live battle payload instead.
    if not gs.is_combat and not (gs.raw.get("battle") or {}).get("enemies"):
        return None
    # Incoming/unblocked are the actual inputs to the block-vs-attack call --
    # without them in the log, diagnosing a bad choice means guessing at
    # thresholds instead of reading the numbers the decision actually saw.
    raw_incoming = combat._incoming_damage(gs.enemies) + combat._hand_curse_damage(gs.hand)
    incoming = combat._mitigated_incoming(gs, gs.enemies, raw_incoming)
    pending = combat._pending_block_from_status(gs)
    current_block = gs.player.get("block", 0) + pending
    return {
        "energy": f"{gs.energy}/{gs.max_energy}",
        "block": current_block,
        "pending_block": pending,
        "incoming": incoming,
        "unblocked": max(0, incoming - current_block),
        "dex": combat._player_dexterity(gs),
        "hand": [f"{c['name']}({c.get('cost')},can_play={c.get('can_play', True)})" for c in gs.hand],
        "enemies": [
            f"{e['name']}({e.get('hp')}hp,atk={combat._enemy_attack_damage(e)})" for e in gs.enemies
        ],
    }


def _decision_fingerprint(gs: GameState) -> tuple:
    """Signature of everything the current screen's decision depends on.

    Catches a failure mode seen live more than once: an action reports "ok"
    while genuinely changing nothing, so the same decision repeats forever.
    Two real instances -- Calculated Gamble in combat (hand/energy/block/enemy
    HP all frozen) and claiming a potion reward with a full potion belt (the
    claim silently no-ops). Distinct from StuckDetector's full-freeze check,
    which needs the *entire* raw payload byte-identical; here the rest of the
    payload keeps ticking, so only the decision-relevant slice is compared.
    """
    if gs.is_combat:
        hand_sig = tuple((c.get("id"), c.get("cost"), c.get("can_play")) for c in gs.hand)
        enemy_sig = tuple(sorted((e.get("entity_id"), e.get("hp"), e.get("block")) for e in gs.enemies))
        return ("combat", gs.energy, gs.player.get("block", 0), hand_sig, enemy_sig)
    # Non-combat screens: the screen's own payload plus the resources a
    # decision there can consume (gold, potion count, HP).
    screen_payload = json.dumps(gs.raw.get(gs.state_type), sort_keys=True, default=str)
    return (gs.state_type, gs.floor, gs.gold, len(gs.potions), gs.hp, screen_payload)


def _dispatch(gs: GameState, prefer_abandon: bool = False) -> tuple[str, dict[str, Any]]:
    st = gs.state_type
    if st in ("monster", "elite", "boss"):
        if gs.is_play_phase:
            return combat.decide(gs)
        return "state", {}
    if st == "hand_select":
        return misc_screens.decide_hand_select(gs)
    if st == "rewards":
        return rewards.decide_rewards(gs)
    if st == "card_reward":
        return rewards.decide_card_reward(gs)
    if st == "map":
        return map_nav.decide_map(gs)
    if st == "event":
        return events.decide_event(gs)
    if st == "rest_site":
        return rest.decide_rest(gs)
    if st == "shop":
        return shop.decide_shop(gs)
    if st == "treasure":
        return misc_screens.decide_treasure(gs)
    if st == "card_select":
        return misc_screens.decide_card_select(gs)
    if st == "bundle_select":
        return misc_screens.decide_bundle_select(gs)
    if st == "relic_select":
        return relics.decide_relic_select(gs)
    if st == "crystal_sphere":
        return misc_screens.decide_crystal_sphere(gs)
    if st == "game_over":
        return menu.decide_game_over(gs)
    if st == "menu":
        return menu.decide_menu(gs, prefer_abandon=prefer_abandon)
    return "state", {}


class BotLoop:
    def __init__(self, client: Optional[ApiClient] = None, max_actions: Optional[int] = None):
        self.client = client or ApiClient()
        self.max_actions = max_actions
        LOG_DIR.mkdir(exist_ok=True)
        self._log_path = LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        self._log_file = open(self._log_path, "a", encoding="utf-8")
        self._prev_summary: Optional[dict[str, Any]] = None
        self._stuck = StuckDetector()
        self._consecutive_recoveries = 0
        self._last_decision_fp: Optional[tuple] = None
        self._last_decision: Optional[tuple[str, tuple]] = None
        self._same_decision_streak = 0
        self._acted_fp: Optional[tuple] = None  # state as of our last successful action
        self._settle_polls = 0
        self._confirming_end_turn = False
        # Set when a run was already in progress at startup: forces the
        # menu handler to abandon it rather than resume it.
        self._force_abandon = False
        # Floors at which a non-zero ascension has already been reported, so
        # the warning is loud but not repeated on every poll.
        self._ascension_warned_run: Optional[int] = None
        self._recent_decisions: deque = deque(maxlen=CYCLE_WINDOW)
        self._recorder = RunRecorder(LOG_DIR, self._log_path.name)

    def _log(self, record: dict[str, Any]) -> None:
        record["ts"] = time.time()
        self._log_file.write(json.dumps(record) + "\n")
        self._log_file.flush()
        event = record.get("event")
        if event:
            self._recorder.note_event(event)

    # Screens where "stuck" usually means the bot cannot satisfy the prompt,
    # not that the engine has frozen. Relaunching a perfectly healthy game
    # throws the run away, so try to leave the screen first.
    _ESCAPABLE_SCREENS = (
        "card_select", "bundle_select", "hand_select", "relic_select",
        "rewards", "treasure", "crystal_sphere", "event", "shop", "rest_site",
    )

    def _try_escape_screen(self, gs: "GameState") -> bool:
        """Attempt to leave a selection screen that will not advance.

        Returns True if an escape action was accepted. A live run sat on
        "Choose 3 cards to Enchant." selecting three cards and confirming 32
        times -- every call returning `ok`, the screen never advancing -- and
        the freeze recovery then killed and relaunched the game repeatedly.
        The game was fine; only the screen was unsatisfiable. Cancelling or
        proceeding costs at most one skipped reward, which is far cheaper than
        losing the run.
        """
        state_type = gs.state_type
        if state_type not in self._ESCAPABLE_SCREENS:
            return False
        section = getattr(gs, state_type, None) or {}
        attempts = []
        if isinstance(section, dict) and section.get("can_cancel"):
            attempts.append(("cancel_selection", {}))
        attempts.append(("proceed", {}))
        for action, fields in attempts:
            self._log({"event": "stuck_escape_attempt", "action": action,
                       "state_type": state_type})
            try:
                result = self.client.post_action(action, **fields)
            except Exception:
                continue
            if result is not None:
                return True
        return False

    def _recover_from_stuck(self, gs: "GameState | None" = None) -> None:
        """A genuine engine freeze has no in-API fix (there is no in-combat
        quit/flee), so the last resort is killing and relaunching and letting
        the run's autosave pick back up.

        Before that, try simply leaving the screen: most "stuck" cases are a
        prompt the bot cannot satisfy rather than a frozen engine, and a
        relaunch there costs the whole run for nothing.
        """
        if gs is not None and self._try_escape_screen(gs):
            self._log({"event": "stuck_escaped_without_relaunch"})
            print("[bot] stuck screen -- escaped without relaunching")
            self._stuck.reset()
            return
        self._consecutive_recoveries += 1
        print(f"[bot] game appears frozen -- recovery attempt {self._consecutive_recoveries}")
        self._log({"event": "stuck_detected", "consecutive_recoveries": self._consecutive_recoveries})
        if self._consecutive_recoveries > MAX_CONSECUTIVE_RECOVERIES:
            self._log({"event": "recovery_exhausted"})
            raise RuntimeError(
                f"Game froze {self._consecutive_recoveries} times in a row even after relaunching -- "
                "giving up rather than looping forever. Needs a manual look."
            )
        kill_and_relaunch(reason="stuck_recovery")
        self.client.wait_until_ready(attempts=RELAUNCH_WAIT_ATTEMPTS, delay=RELAUNCH_WAIT_DELAY)
        self._stuck.reset()

    def _check_ascension(self, gs: GameState) -> None:
        """Flag any run that is not Ascension 0.

        Every run is meant to be Ascension 0, and the bot has no way to pick
        anything else: the API's `character_select` screen offers only the
        characters plus `confirm`/`embark`/`back` -- there is no ascension or
        difficulty control anywhere in the menu flow, and all 51 runs recorded
        so far came back as ascension 0.

        So this is a tripwire, not a setting. If the game ever starts handing
        out a different ascension (unlocked later, or remembered from a
        human-played run), the results would silently stop being comparable
        with everything already recorded. Deliberately does *not* abandon the
        run: reaching the main menu means relaunching the game, and if the
        ascension were sticky that would relaunch forever. Better to finish
        the run loudly marked than to spin.
        """
        if gs.ascension == 0 or gs.floor <= 0:
            return
        if self._ascension_warned_run == gs.floor:
            return
        self._ascension_warned_run = gs.floor
        print(f"[bot] WARNING: run is Ascension {gs.ascension}, not 0 -- results are not comparable")
        self._log({"event": "unexpected_ascension", "ascension": gs.ascension, "floor": gs.floor})

    def _abandon_preexisting_run(self) -> None:
        """Throw away a run that was already in progress when we started.

        A half-played run is not a measurement of the current build -- it may
        have been played by older code, by hand, or interrupted partway -- so
        counting it pollutes the results. Restarting mid-run also used to
        leave deck memory cold, degrading every deck-dependent decision until
        the next combat.

        There is no "open the menu" action in the API, so the only route to
        the main menu (where `abandon_run` lives) is to relaunch the game.
        The abandoned run is never recorded because `RunRecorder` only writes
        a summary at `game_over`, which we never reach.
        """
        try:
            raw = self.client.get_state()
        except (ApiError, requests.exceptions.RequestException):
            return
        if "error" in raw:
            return

        gs = GameState(raw)
        in_a_run = gs.floor > 0 and gs.state_type not in ("menu", "game_over")
        if not in_a_run:
            return

        print(f"[bot] a run was already in progress (act {gs.act} floor {gs.floor}) -- abandoning it; it will not be counted")
        self._log({"event": "abandoning_preexisting_run", "state": _summary(gs)})

        # Relaunch to reach the main menu, then let the menu handler pick
        # `abandon_run` via the same preference the recovery path uses.
        deck_memory.reset()  # that deck belongs to a run we're discarding
        kill_and_relaunch(reason="abandon_preexisting_run")
        try:
            self.client.wait_until_ready(attempts=RELAUNCH_WAIT_ATTEMPTS, delay=RELAUNCH_WAIT_DELAY)
        except ApiError:
            return
        self._force_abandon = True

    def _confirm_abandon_popup(self) -> None:
        """Answer the yes/no confirmation that `abandon_run` opens.

        Verified against the live game: the popup reports
        `menu_screen: "popup"` with yes/no options, and replying late gets
        "Unknown menu option: yes" -- the mod no longer sees a popup and falls
        through to main-menu handling, leaving the run un-abandoned. So this
        answers straight away rather than going round the normal loop.
        """
        for _ in range(ABANDON_CONFIRM_ATTEMPTS):
            try:
                raw = self.client.get_state()
            except (ApiError, requests.exceptions.RequestException):
                return
            if raw.get("menu_screen") != "popup":
                time.sleep(ABANDON_CONFIRM_POLL)
                continue
            try:
                self.client.post_action("menu_select", option="yes")
                self._log({"event": "abandon_confirmed"})
            except (ApiError, requests.exceptions.RequestException) as exc:
                self._log({"event": "abandon_confirm_failed", "error": str(exc)})
            self._force_abandon = False  # the old run is gone; play normally now
            return
        # No popup appeared -- the abandon either completed or never took.
        self._force_abandon = False

    def run(self) -> None:
        print(f"[bot] logging to {self._log_path}")
        # Restore the deck a previous process learned, so a mid-run restart
        # doesn't leave shop/campfire/reward decisions blind until the next
        # combat repopulates the piles.
        deck_memory.load()
        self.client.wait_until_ready()
        self._abandon_preexisting_run()
        actions_taken = 0
        while self.max_actions is None or actions_taken < self.max_actions:
            try:
                raw = self.client.get_state()
            except Exception as e:  # transient connection hiccups -- keep polling
                self._log({"event": "get_state_error", "error": str(e)})
                time.sleep(ERROR_BACKOFF)
                continue

            if "error" in raw:
                self._log({"event": "state_error", "error": raw.get("error")})
                time.sleep(ERROR_BACKOFF)
                continue

            # Remember the act's boss from the map so deck building can aim
            # at the fight the act is actually heading toward. Takes the raw
            # payload: `gs` is not built until below.
            boss_intel.note_raw(raw)

            if self._stuck.observe(raw):
                self._recover_from_stuck(GameState(raw))
                continue

            gs = GameState(raw)
            self._check_ascension(gs)
            # Snapshot the deck while combat makes it visible, so shops and
            # rest sites can still see it.
            deck_memory.remember(gs)

            if raw.get("ready_for_command") is False:
                time.sleep(POLL_IDLE_SLEEP)
                continue

            # Settle gate: if the state still looks exactly as it did when we
            # last acted, that action hasn't landed yet -- poll instead of
            # acting again. Cheap polls beat firing cards into a state that
            # hasn't caught up (Grand Finale ends the fight instantly but the
            # old combat state lingers for a beat). Bounded, so a genuinely
            # no-op action still falls through to the stale-escape below.
            if self._acted_fp is not None and _decision_fingerprint(gs) == self._acted_fp:
                if self._settle_polls < SETTLE_MAX_POLLS:
                    self._settle_polls += 1
                    time.sleep(SETTLE_POLL_DELAY)
                    continue
            else:
                self._acted_fp = None
                self._settle_polls = 0

            outcome = None
            if self._prev_summary is not None:
                cur = _summary(gs)
                outcome = {k: cur[k] - self._prev_summary[k] for k in ("hp", "gold", "floor") if k in cur}

            prefer_abandon = (
                self._force_abandon
                or self._consecutive_recoveries >= ABANDON_AFTER_N_RECOVERIES
            )
            action, fields = _dispatch(gs, prefer_abandon=prefer_abandon)

            # Ending a turn is irreversible, so confirm it against a fresh
            # poll first. Effects can land *after* we read the state: a
            # discarded Sly card (Tactician: "Sly. Gain 1 Energy") plays
            # itself for free a beat later, and the bot ended the turn on the
            # pre-effect snapshot -- with the energy, and the cards it could
            # then afford, still sitting in hand.
            if action == "end_turn" and gs.is_combat and not self._confirming_end_turn:
                self._confirming_end_turn = True
                time.sleep(END_TURN_CONFIRM_DELAY)
                try:
                    fresh = self.client.get_state()
                except (ApiError, requests.exceptions.RequestException):
                    fresh = None
                if fresh and "error" not in fresh:
                    fresh_gs = GameState(fresh)
                    if fresh_gs.is_combat:
                        recheck, recheck_fields = _dispatch(fresh_gs, prefer_abandon=prefer_abandon)
                        if recheck != "end_turn":
                            # Something became playable -- act on it instead.
                            gs, action, fields = fresh_gs, recheck, recheck_fields
                self._confirming_end_turn = False

            # "state" is a deliberate no-op poll (enemy's turn, transitional
            # screen), not a decision that could be stuck. Bail out before the
            # stale/cycle detectors so they never see it: counting polls as
            # repeated decisions made them force an end_turn mid-fight, once
            # dumping 3 energy and three playable Defends into a 23-damage
            # hit. The real-freeze case is StuckDetector's job, not theirs.
            if action == "state":
                self._prev_summary = _summary(gs)
                time.sleep(POLL_IDLE_SLEEP)
                continue

            decision_fp = _decision_fingerprint(gs)
            decision_sig = (action, tuple(sorted(fields.items())))
            if decision_fp == self._last_decision_fp and decision_sig == self._last_decision:
                self._same_decision_streak += 1
            else:
                self._same_decision_streak = 0
            self._last_decision_fp = decision_fp
            self._last_decision = decision_sig

            if self._same_decision_streak >= STALE_ACTION_LIMIT:
                # Same action against an unchanged decision-relevant state
                # this many times in a row -- whatever it was meant to do
                # isn't happening. Escape via whatever moves this screen
                # along: end_turn in combat, proceed everywhere else (the
                # potion-belt-full reward loop needed the latter; end_turn
                # isn't even a valid action outside combat).
                escape = ("end_turn", {}) if gs.is_combat else ("proceed", {})
                self._log(
                    {
                        "event": "stale_action_detected",
                        "state_type": gs.state_type,
                        "action": action,
                        "fields": fields,
                        "streak": self._same_decision_streak,
                        "escape": escape[0],
                    }
                )
                action, fields = escape
                self._same_decision_streak = 0
                self._recent_decisions.clear()

            # Cycle escape: a full window of decisions made up of only a
            # couple of distinct ones is a ping-pong making no progress, even
            # though each individual step "works" and the state does change.
            self._recent_decisions.append((gs.state_type, action, tuple(sorted(fields.items()))))
            if (
                len(self._recent_decisions) == CYCLE_WINDOW
                and len(set(self._recent_decisions)) <= CYCLE_DISTINCT_MAX
                and action != "proceed"
            ):
                self._log(
                    {
                        "event": "cycle_detected",
                        "state_type": gs.state_type,
                        "decisions": [list(d) for d in set(self._recent_decisions)],
                    }
                )
                action, fields = ("end_turn", {}) if gs.is_combat else ("proceed", {})
                self._recent_decisions.clear()

            record = {
                "state": _summary(gs),
                "combat": _combat_detail(gs),
                # Every option that was on offer, so later analysis can see
                # what the bot passed up, not just what it chose.
                "options": _available_options(gs),
                "screen_raw": _unverified_screen_detail(gs),
            "raw": _raw_for_replay(gs),
                "action": action,
                "fields": fields,
                "outcome_since_last": outcome,
            }

            try:
                self.client.post_action(action, **fields)
                record["result"] = "ok"
                # The divination screen's click action name is unverified, so
                # the handler probes candidates -- remember whichever the game
                # actually accepted.
                misc_screens.note_crystal_sphere_result(action, True)
                self._consecutive_recoveries = 0  # real progress -- forgive past freezes
                if action == "menu_select" and fields.get("option") == "abandon_run":
                    # abandon_run only opens a yes/no confirmation, and that
                    # popup is short-lived: verified live that a delayed reply
                    # gets "Unknown menu option: yes" (it falls through to the
                    # main menu) and the run survives. Answer it immediately,
                    # before the normal settle/poll cycle can miss the window.
                    self._confirm_abandon_popup()
                # Remember the pre-action state so the settle gate above can
                # tell "hasn't landed yet" from "landed, nothing changed".
                self._acted_fp = decision_fp
                self._settle_polls = 0
                # Let the effect/animation actually resolve before polling --
                # hand-churning cards need longer than a plain attack.
                time.sleep(_settle_delay_for(gs, action, fields))
            except (ApiError, requests.exceptions.RequestException) as e:
                # RequestException covers network-level failures (timeouts,
                # connection resets) -- these happen for real, e.g. right after
                # a recovery relaunch while the game is still settling, and an
                # uncaught one previously crashed the whole bot process.
                record["result"] = "error"
                record["error"] = str(e)
                time.sleep(ERROR_BACKOFF)

            self._log(record)
            # Run-level bookkeeping: tracks run boundaries and writes one
            # summary line per finished run to logs/runs.jsonl.
            self._recorder.observe(gs, action, fields)
            print(f"[{gs.state_type}] act{gs.act} f{gs.floor} hp{gs.hp}/{gs.max_hp} -> {action}{fields}")
            self._prev_summary = _summary(gs)
            actions_taken += 1

    def close(self) -> None:
        self._log_file.close()
