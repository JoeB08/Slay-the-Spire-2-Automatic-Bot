"""Watch a *human-played* run and record what the bot would have done.

Read-only: this polls `GET /api/v1/singleplayer` and never posts an action. It
deliberately uses `requests.get` directly rather than `ApiClient`, which has a
`post_action` method -- there is no code path here that can act.

Read-only is **not** the same as harmless. The mod serialises the entire game
state on every GET, so polling too hard starves the game: a human player hit a
locked shop screen twice, and it cleared the instant this stopped. Hence the
adaptive rate below, and the sleep on every path out of the loop.

At every decision point it records three things:
  * the screen and the options actually on offer,
  * what the bot's own strategy would have chosen (`loop._dispatch`), and
  * the raw state, so `shadow_analyze.py` can reconstruct what *you* chose by
    diffing against the next recorded state.

Usage (stop the bot first -- two things driving one game fight each other):
    python scripts/shadow_record.py
    python scripts/shadow_record.py --out logs/my_run.jsonl

Ctrl-C to stop. Then:
    python scripts/shadow_analyze.py logs/shadow_<timestamp>.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import deck_memory  # noqa: E402
from bot.game_state import GameState  # noqa: E402
from bot.loop import (  # noqa: E402
    _available_options,
    _combat_detail,
    _decision_fingerprint,
    _dispatch,
    _summary,
)

API_URL = "http://localhost:15526/api/v1/singleplayer"

# Polling is not free. The mod serialises the whole game state on every GET,
# and at 0.4s that was enough to lock up the shop screen for a human player --
# twice, reproducibly, and it cleared the moment the recorder stopped. So the
# rate is adaptive: fast during combat, where states change quickly and the
# granularity is the whole point, and slow everywhere else, where the screen
# sits still and one snapshot per change is all the analysis needs.
POLL_SECONDS = 0.4          # combat
SLOW_POLL_SECONDS = 2.5     # map, events, rewards
IDLE_POLL_SECONDS = 5.0     # shop, rest site -- the screens that actually locked up
FAST_SCREENS = {"monster", "elite", "boss", "hand_select", "card_select"}
# Normally a shop's contents don't change while you browse, and a purchase
# leaves a permanent mark (`is_stocked` flips false and stays false), so entry
# and exit snapshots are enough to reconstruct what was bought. This is the
# screen a human found locked twice, hence the heavy back-off.
#
# The exception is a restocking relic (the Courier replaces each card you buy),
# where the shelf genuinely changes mid-visit and a 5s gap could miss an item
# appearing and being bought. Detected from relic text rather than a name, so
# an equivalent relic counts too.
IDLE_SCREENS = {"shop", "rest_site"}
_RESTOCK_HINTS = ("restock", "replaces it", "replace it", "new card")


def _shop_restocks(gs) -> bool:
    for relic in gs.relics:
        text = (relic.get("description") or "").lower()
        if any(h in text for h in _RESTOCK_HINTS):
            return True
    return False

# Screens where a choice is actually being made. Everything else (loading,
# animations, the enemy's phase) is noise for this purpose.
DECISION_SCREENS = {
    "monster", "elite", "boss", "hand_select", "card_select", "card_reward",
    "rewards", "map", "event", "rest_site", "shop", "treasure",
    "relic_select", "bundle_select", "crystal_sphere",
}


def _poll() -> dict | None:
    try:
        r = requests.get(API_URL, timeout=3)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a human run + the bot's shadow decisions")
    parser.add_argument("--out", default=None, help="output .jsonl (default: logs/shadow_<timestamp>.jsonl)")
    args = parser.parse_args()

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent / "logs" /
        f"shadow_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    )
    out.parent.mkdir(exist_ok=True)

    print(f"[shadow] recording to {out}")
    print("[shadow] read-only -- this never sends an action. Ctrl-C to stop.")

    deck_memory.load()
    last_fp = None
    recorded = 0

    try:
        while True:
            raw = _poll()
            if not raw or "error" in raw:
                time.sleep(POLL_SECONDS)
                continue

            gs = GameState(raw)
            # Keep the bot's deck knowledge current the same way the live loop
            # does, so its shadow decisions are made on the same information.
            deck_memory.remember(gs)

            if gs.state_type in FAST_SCREENS:
                delay = POLL_SECONDS
            elif gs.state_type in IDLE_SCREENS:
                # A restocking relic means the shelf really does change while
                # you shop, so don't go as quiet.
                delay = SLOW_POLL_SECONDS if _shop_restocks(gs) else IDLE_POLL_SECONDS
            else:
                delay = SLOW_POLL_SECONDS

            if gs.state_type not in DECISION_SCREENS or raw.get("ready_for_command") is False:
                time.sleep(delay)
                continue

            fp = _decision_fingerprint(gs)
            if fp == last_fp:
                time.sleep(delay)
                continue
            last_fp = fp

            try:
                action, fields = _dispatch(gs)
            except Exception as e:  # a shadow decision must never stop the recording
                action, fields = "error", {"error": repr(e)}

            if action == "state":
                # Not a decision (the enemy is acting, a screen is settling).
                # This used to `continue` *without sleeping*, turning the
                # recorder into an unthrottled busy-loop that hammered the API
                # as fast as it could answer -- a far bigger load than the
                # nominal poll rate suggests, and the likeliest reason a human
                # player found the shop screen locked up.
                time.sleep(delay)
                continue

            record = {
                "ts": time.time(),
                "state": _summary(gs),
                "options": _available_options(gs),
                "combat": _combat_detail(gs),
                "bot_action": action,
                "bot_fields": fields,
                # Needed to reconstruct what the human actually did.
                "raw": raw,
            }
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            recorded += 1
            print(f"[shadow] {recorded:4d}  {gs.state_type:12} act{gs.act} f{gs.floor}  "
                  f"bot would: {action} {fields}")

            time.sleep(delay)
    except KeyboardInterrupt:
        print(f"\n[shadow] stopped. {recorded} decision points recorded to {out}")


if __name__ == "__main__":
    main()
