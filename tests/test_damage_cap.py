"""Enemies with a per-turn HP-loss cap.

Skulking Colony (the new Act 1's floor-7 elite) carries `Hardened Shell`:
"Skulking Colony cannot lose more than 20 HP each turn." The status `amount`
is the *remaining* allowance, logged counting down 20 -> 17 -> 14 -> 11 within
one turn. Damage above it is discarded by the game, so pouring a big attack in
wastes the difference, and a kill that needs more than the allowance is simply
not available this turn.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

SHELL_TEXT = "Skulking Colony cannot lose more than 20 HP each turn."
BIG = {"name": "Big Hit", "cost": "1", "description": "Deal 40 damage.", "target_type": "AnyEnemy"}
SMALL = {"name": "Jab", "cost": "1", "description": "Deal 6 damage.", "target_type": "AnyEnemy"}


def _colony(hp=75, remaining=20, attack=None):
    return {
        "entity_id": "SC_0", "name": "Skulking Colony", "hp": hp, "max_hp": 75, "block": 0,
        "status": [{"name": "Hardened Shell", "amount": remaining, "type": "Buff",
                    "description": SHELL_TEXT}],
        "intents": ([{"type": "Attack", "label": str(attack), "title": "", "description": ""}]
                    if attack else [{"type": "Unknown", "label": "", "title": "", "description": ""}]),
    }


def _plain(hp=75):
    return {"entity_id": "P_0", "name": "Plain", "hp": hp, "max_hp": 75, "block": 0,
            "status": [], "intents": []}


def test_allowance_is_the_remaining_amount_not_the_printed_cap():
    assert combat._damage_allowance(_colony(remaining=20)) == 20
    assert combat._damage_allowance(_colony(remaining=11)) == 11
    assert combat._damage_allowance(_plain()) is None


def test_damage_above_the_allowance_is_discarded():
    assert combat._capped_damage(_colony(remaining=11), 30) == 11
    assert combat._capped_damage(_colony(remaining=20), 8) == 8
    assert combat._capped_damage(_plain(), 30) == 30


def test_a_kill_needing_more_than_the_allowance_is_not_claimed():
    """Without this the bot commits the whole turn to a kill the cap forbids."""
    assert combat._kills_enemy(BIG, _colony(hp=45, remaining=20), 0) is False
    # Within the allowance, the kill is real.
    assert combat._kills_enemy(BIG, _colony(hp=18, remaining=20), 0) is True


def test_ranking_stops_preferring_an_oversized_attack_once_the_cap_bites():
    capped = _colony(remaining=6)
    big = combat._ranking_damage(BIG, capped, 0, None, 1.0)
    small = combat._ranking_damage(SMALL, capped, 0, None, 1.0)
    assert big == small == 6  # both land exactly the allowance
    # Uncapped, the big hit is plainly better again.
    assert combat._ranking_damage(BIG, _plain(), 0, None, 1.0) > combat._ranking_damage(
        SMALL, _plain(), 0, None, 1.0
    )
