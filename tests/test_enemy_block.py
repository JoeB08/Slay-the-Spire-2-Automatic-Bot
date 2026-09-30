import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
DEADLY_POISON = {"name": "Deadly Poison", "cost": "1", "description": "Apply 5 Poison."}


def _enemy(entity_id, hp, block=0, poison=0):
    status = []
    if poison:
        status.append({"id": "POISON_POWER", "name": "Poison", "amount": poison})
    return {
        "entity_id": entity_id,
        "name": entity_id,
        "hp": hp,
        "max_hp": 99,
        "block": block,
        "status": status,
        "intents": [],
    }


def _state(cards, enemies, energy=3):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    raw["battle"]["enemies"] = enemies
    return GameState(raw)


def test_damage_to_kill_includes_enemy_block():
    assert combat._damage_to_kill(_enemy("E", hp=5, block=20)) == 25
    assert combat._damage_to_kill(_enemy("E", hp=5)) == 5


def test_targets_the_unblocked_enemy_over_a_lower_hp_blocked_one():
    # 3 HP behind 30 block is far harder to kill than 12 HP unblocked, but
    # raw-HP targeting sent every attack into the blocked one.
    gs = _state(
        [STRIKE],
        [_enemy("BLOCKED_0", hp=3, block=30), _enemy("OPEN_0", hp=12)],
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["target"] == "OPEN_0"


def test_lethal_check_respects_enemy_block():
    # 6 damage does not kill 5 HP behind 10 block, so this must not be
    # treated as a kill.
    blocked = _enemy("E", hp=5, block=10)
    assert combat._kills_enemy(STRIKE, blocked, 0) is False
    assert combat._kills_enemy(STRIKE, _enemy("E", hp=5), 0) is True


def test_poison_ignores_enemy_block_for_lethal():
    # Poison bypasses Block entirely, so 5 poison kills a 5 HP enemy no
    # matter how much block it is hiding behind.
    blocked = _enemy("E", hp=5, block=30)
    assert combat._kills_enemy(DEADLY_POISON, blocked, 0) is True


def test_existing_poison_still_counts_under_block():
    # 8 HP with 4 poison already ticking needs only 4 more, plus block.
    enemy = _enemy("E", hp=8, block=2, poison=4)
    assert combat._damage_to_kill(enemy) == 6
