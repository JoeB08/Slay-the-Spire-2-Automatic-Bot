"""Enemies that stun themselves at an HP threshold.

Terror Eel (new Act 1) carries `Shriek`: "The first time Terror Eel's HP
reaches 70 or below, it becomes Stunned", and Stun is "Prevent the enemy from
acting on its next turn." Crossing that line cancels a whole turn of damage,
but the bot treated 71 HP and 69 HP as identical. Same shape as the act-1 boss
interrupt (Ceremonial Beast at 150).
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

SHRIEK = {
    "name": "Shriek",
    "amount": 70,
    "description": "The first time Terror Eel's HP reaches 70 or below, it becomes Stunned.",
    "keywords": [{"name": "Stun", "description": "Prevent the enemy from acting on its next turn."}],
}
BIG_HIT = {"name": "Big Hit", "cost": "1", "description": "Deal 30 damage.", "target_type": "AnyEnemy"}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}


def _eel(hp, block=0, attack=20, status=None):
    return {
        "entity_id": "EEL_0", "name": "Terror Eel", "hp": hp, "max_hp": 140, "block": block,
        "status": [copy.deepcopy(SHRIEK)] if status is None else status,
        "intents": [{"type": "Attack", "label": str(attack), "title": "", "description": ""}],
    }


def test_reads_the_threshold_from_the_status_text():
    assert combat._stun_threshold(_eel(140)) == 70
    assert combat._stun_threshold(_eel(140, status=[])) is None


def test_damage_needed_counts_block_and_stops_once_below():
    assert combat._damage_to_stun(_eel(100)) == 30
    assert combat._damage_to_stun(_eel(100, block=12)) == 42
    assert combat._damage_to_stun(_eel(68)) is None  # already under the line


def _state(cards, enemies, hp=70, energy=3):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"].update({"hand": hand, "energy": energy, "hp": hp, "max_hp": 70,
                          "block": 0, "status": [], "potions": []})
    raw["battle"]["enemies"] = enemies
    return GameState(raw)


def test_attacks_to_trip_the_stun_rather_than_blocking():
    # 95 HP, threshold 70 -> 25 damage trips it, and Big Hit does 30.
    gs = _state([BIG_HIT, DEFEND], [_eel(95, attack=20)])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Big Hit"
    assert fields["target"] == "EEL_0"


def test_does_not_chase_a_threshold_it_cannot_reach():
    # 140 HP needs 70 damage; a single 30-damage card cannot get there, so the
    # stun step must stand down and let the normal survive/block logic run.
    gs = _state([BIG_HIT, DEFEND], [_eel(140, attack=20)], energy=1)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Defend"
