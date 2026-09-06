"""Menu navigation, including driving the run-start flow and re-launching a
new run after game over so the bot can loop indefinitely unattended.
"""
from __future__ import annotations

from typing import Any

from ..game_state import GameState

CHARACTER_ID = "SILENT"
SKIP_OPTION_NAMES = {"quit", "settings", "multiplayer", "daily", "custom"}

# The character_select screen doesn't expose which character is currently
# highlighted -- confirm/embark being enabled just means *some* character is
# selected, not necessarily Silent (a live run confirmed this the hard way:
# it picked up Regent as the default/retained selection and confirmed
# straight into it). So we track across polls whether *we've* explicitly
# selected Silent on this visit to the screen, and never confirm before that.
_silent_selected_this_visit = False


def _option_names(options: Any) -> list[str]:
    if not options:
        return []
    if isinstance(options[0], str):
        return list(options)
    return [o.get("name") for o in options if o.get("enabled", True)]


def decide_menu(gs: GameState, prefer_abandon: bool = False) -> tuple[str, dict[str, Any]]:
    """`prefer_abandon`: set by the recovery path (bot/recovery.py) when a
    resumed run has frozen again right away, so we stop trying to continue a
    run that's stuck for good and start fresh instead of looping forever."""
    global _silent_selected_this_visit
    screen = gs.menu_screen
    options = gs.options or []
    names = _option_names(options)

    if screen != "character_select":
        _silent_selected_this_visit = False  # reset for the next time we land here

    if screen == "popup":
        # Confirmation dialogs (abandon_run raises a yes/no). Only confirm
        # when we actually asked for the destructive thing -- the generic
        # fallback below would happily answer "yes" to any popup, which
        # is how an accidental confirm gets clicked.
        if prefer_abandon:
            for option in ("yes", "confirm", "ok"):
                if option in names:
                    return "menu_select", {"option": option}
        for option in ("no", "cancel", "back"):
            if option in names:
                return "menu_select", {"option": option}
        return "state", {}  # unknown popup -- poll rather than guess

    if screen == "main":
        if prefer_abandon and "abandon_run" in names:
            return "menu_select", {"option": "abandon_run"}
        if "continue" in names:
            return "menu_select", {"option": "continue"}
        if "singleplayer" in names:
            return "menu_select", {"option": "singleplayer"}

    if screen == "singleplayer":
        if "standard" in names:
            return "menu_select", {"option": "standard"}

    if screen == "timeline":
        # New character/relic/etc unlocks earned by a run sit "obtained but
        # unrevealed" here and need an explicit advance to reveal (a known
        # upstream pain point -- STS2MCP issues #92/#93 describe exactly this
        # blocking automated play). Advance while there's something pending,
        # then back out rather than spamming advance forever once there isn't.
        if gs.raw.get("obtained_unrevealed_count", 0) > 0 and "advance" in names:
            return "menu_select", {"option": "advance"}
        if "back" in names:
            return "menu_select", {"option": "back"}

    if screen == "character_select":
        if not _silent_selected_this_visit:
            if CHARACTER_ID not in names:
                # Not currently selectable -- don't guess a different
                # character. Poll again rather than confirming into the wrong one.
                return "state", {}
            _silent_selected_this_visit = True
            return "menu_select", {"option": CHARACTER_ID}
        if "confirm" in names:
            return "menu_select", {"option": "confirm"}
        if "embark" in names:
            return "menu_select", {"option": "embark"}
        return "state", {}

    # Fallback: pick the first enabled, non-skippable option so we don't stall
    # on menu screens we haven't specifically modeled (timeline, settings, etc).
    for name in names:
        if name and name.lower() not in SKIP_OPTION_NAMES:
            return "menu_select", {"option": name}
    if names:
        return "menu_select", {"option": names[0]}
    # No actionable option (usually a transitional screen mid-animation).
    # Poll rather than firing `proceed`, which isn't valid on menu screens and
    # just produced a burst of errors in a live run.
    return "state", {}


def decide_game_over(gs: GameState) -> tuple[str, dict[str, Any]]:
    # Real payload nests options under game_over.options, not top-level
    # `options` like the menu screens do -- that mismatch had this stuck in
    # an infinite "proceed" error loop (not a valid action on this screen).
    names = _option_names(gs.raw.get("game_over", {}).get("options") or [])
    for preferred in ("continue", "main_menu", "menu"):
        if preferred in names:
            return "menu_select", {"option": preferred}
    if names:
        return "menu_select", {"option": names[0]}
    return "state", {}  # transitional -- poll instead of erroring on `proceed`
