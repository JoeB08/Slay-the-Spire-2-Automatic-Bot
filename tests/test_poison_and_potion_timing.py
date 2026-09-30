import copy

from bot.game_state import GameState
from bot.strategy import combat
from bot.strategy import potions as potion_db
from conftest import load_fixture

DEADLY_POISON = {"name": "Deadly Poison", "cost": "1", "description": "Apply 5 Poison."}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}

FLEX = {"id": "FLEX_POTION", "name": "Flex Potion", "slot": 0, "can_use_in_combat": True,
        "target_type": "AnyPlayer",
        "description": "Gain 5 Strength. At the end of your turn, lose 5 Strength."}
SPEED = {"id": "SPEED_POTION", "name": "Speed Potion", "slot": 0, "can_use_in_combat": True,
         "target_type": "AnyPlayer",
         "description": "Gain 5 Dexterity. At the end of your turn, lose 5 Dexterity."}
FIRE = {"id": "FIRE_POTION", "name": "Fire Potion", "slot": 0, "can_use_in_combat": True,
        "target_type": "AnyEnemy", "description": "Deal 20 damage."}


def _state(cards, enemy_hp=999, attack=None, potions=None, hp=70, energy=3, state_type="monster",
           enemy_poison=0):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["state_type"] = state_type
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["hp"] = hp
    raw["player"]["max_hp"] = 70
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    raw["player"]["potions"] = potions or []
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["status"] = (
        [{"id": "POISON_POWER", "name": "Poison", "amount": enemy_poison}] if enemy_poison else []
    )
    enemy["intents"] = (
        [{"type": "Attack", "label": str(attack), "title": "", "description": ""}] if attack else []
    )
    return GameState(raw)


# --- poison counts toward lethal ---

def test_poison_applied_counts_as_lethal():
    # 5 HP enemy, Deadly Poison applies 5 -- it dies before acting, so this
    # should register as a kill even though the card deals no direct damage.
    gs = _state([DEADLY_POISON], enemy_hp=5)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0


def test_poison_card_counts_as_an_attack_option():
    assert combat._is_attack_option(DEADLY_POISON) is True
    assert combat._card_poison(DEADLY_POISON) == 5


def test_poison_stacks_with_existing_poison_for_lethal():
    # 8 HP with 4 poison already ticking -> effective HP 4, so 5 more kills.
    gs = _state([DEADLY_POISON], enemy_hp=8, enemy_poison=4)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0


def test_vulnerable_does_not_inflate_poison():
    enemy = {"entity_id": "E", "hp": 50, "status": [{"id": "VULNERABLE_POWER", "name": "Vulnerable", "amount": 1}]}
    # Pure poison card: 5 poison, unscaled by Vulnerable.
    assert combat._effective_damage(DEADLY_POISON, enemy, 0) == 5
    # Attack damage is scaled: 6 * 1.5.
    assert combat._effective_damage(STRIKE, enemy, 0) == 9


# --- turn-scoped potion timing ---

def test_flex_potion_is_recognised_as_turn_scoped():
    assert potion_db.is_turn_scoped_buff(FLEX) is True
    assert potion_db.is_turn_scoped_buff(SPEED) is True
    assert potion_db.is_turn_scoped_buff(FIRE) is False


def test_does_not_burn_flex_potion_at_the_start_of_an_elite_fight():
    # Previously "strength" matched the offensive hints and got spent turn 1
    # of an elite for nothing.
    gs = _state([STRIKE], enemy_hp=999, potions=[dict(FLEX)], state_type="elite")
    assert potion_db.suggest_potion_use(gs) is None


def test_uses_flex_potion_when_it_converts_into_a_kill():
    # One Strike does 6; the enemy has 10. +5 Strength makes it 11 -> lethal.
    gs = _state([STRIKE], enemy_hp=10, potions=[dict(FLEX)], energy=1)
    result = potion_db.suggest_potion_use(gs)
    assert result is not None
    assert result[0] == "use_potion"


def test_does_not_burn_speed_potion_when_block_is_not_needed():
    gs = _state([DEFEND], enemy_hp=999, potions=[dict(SPEED)], state_type="elite")
    assert potion_db.suggest_potion_use(gs) is None


def test_uses_speed_potion_when_the_extra_dexterity_covers_the_hit():
    # One Defend gives 5; incoming is 9. +5 Dexterity makes it 10 -> covered.
    gs = _state([DEFEND], enemy_hp=999, attack=9, potions=[dict(SPEED)], energy=1, hp=30)
    result = potion_db.suggest_potion_use(gs)
    assert result is not None
    assert result[0] == "use_potion"
