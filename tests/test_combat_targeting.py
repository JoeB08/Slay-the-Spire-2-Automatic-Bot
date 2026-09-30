import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

NEUTRALIZE = {"name": "Neutralize", "cost": "0", "description": "Deal 3 damage. Apply 1 Weak."}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}
SURVIVOR = {"name": "Survivor", "cost": "1", "description": "Gain 8 Block.", "target_type": "Self"}


def _enemy(entity_id, hp, attack=None):
    return {
        "entity_id": entity_id,
        "name": entity_id,
        "hp": hp,
        "max_hp": 99,
        "block": 0,
        "status": [],
        "intents": (
            [{"type": "Attack", "label": str(attack), "title": "", "description": ""}]
            if attack
            else [{"type": "StatusCard", "label": "1", "title": "", "description": ""}]
        ),
    }


def _state(cards, enemies, player_hp=70, max_hp=70, energy=3, dexterity=0, block=0):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["max_energy"] = 3
    raw["player"]["hp"] = player_hp
    raw["player"]["max_hp"] = max_hp
    raw["player"]["block"] = block
    raw["player"]["status"] = (
        [{"id": "DEXTERITY_POWER", "name": "Dexterity", "amount": dexterity}] if dexterity else []
    )
    raw["battle"]["enemies"] = enemies
    return GameState(raw)


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


def test_weak_targets_an_attacking_enemy_not_the_lowest_hp_one():
    # Weak only reduces Attack damage -- applying it to the non-attacking
    # low-HP enemy (the default target) does nothing.
    gs = _state(
        [NEUTRALIZE],
        [_enemy("PASSIVE_0", hp=5, attack=None), _enemy("ATTACKER_0", hp=40, attack=12)],
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["target"] == "ATTACKER_0"


def test_weak_prefers_the_biggest_attacker():
    gs = _state(
        [NEUTRALIZE],
        [_enemy("SMALL_0", hp=40, attack=3), _enemy("BIG_0", hp=40, attack=15)],
    )
    action, fields = combat.decide(gs)
    assert fields["target"] == "BIG_0"


def test_weak_falls_back_to_lowest_hp_when_nobody_is_attacking():
    gs = _state(
        [NEUTRALIZE],
        [_enemy("A_0", hp=40, attack=None), _enemy("B_0", hp=9, attack=None)],
    )
    action, fields = combat.decide(gs)
    assert fields["target"] == "B_0"


LEG_SWEEP = {"name": "Leg Sweep", "cost": "2", "description": "Apply 2 Weak. Gain 11 Block."}
MALAISE = {"name": "Malaise", "cost": "1", "description": "Enemy loses X Strength. Apply X Weak. Exhaust."}
TRACKING = {"name": "Tracking", "cost": "2", "description": "Weak enemies take double damage from Attacks."}


def test_weak_applying_skill_targets_the_attacker_not_lowest_hp():
    # Leg Sweep applies Weak but deals no damage, so it routes through the
    # *value* step -- which used to target lowest-HP unconditionally. That's
    # how Weak kept landing on enemies that weren't attacking.
    gs = _state(
        [LEG_SWEEP],
        [_enemy("PASSIVE_0", hp=5, attack=None), _enemy("ATTACKER_0", hp=40, attack=12)],
        player_hp=20,
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["target"] == "ATTACKER_0"


def test_x_cost_weak_card_is_detected():
    # "Apply X Weak" (Malaise) was missed by a digits-only pattern.
    assert combat._card_applies_weak(MALAISE) is True


def test_card_merely_mentioning_weak_is_not_treated_as_applying_it():
    assert combat._card_applies_weak(TRACKING) is False


def test_plain_attack_still_targets_lowest_hp():
    gs = _state(
        [STRIKE],
        [_enemy("LOW_0", hp=40, attack=None), _enemy("HIGH_0", hp=80, attack=9)],
    )
    action, fields = combat.decide(gs)
    assert fields["target"] == "LOW_0"


def test_reserves_energy_to_block_instead_of_spending_it_all_attacking():
    # 1 energy, 4 incoming, a Defend and a Strike. Previously the attack ate
    # the last energy every turn and the chip damage piled up.
    gs = _state([DEFEND, STRIKE], [_enemy("E_0", hp=99, attack=4)], energy=1)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


def test_still_attacks_through_trivial_chip_damage():
    # 1 incoming vs a 5-block Defend is an inefficient trade -- take the hit
    # and deal damage instead (the take-1-to-deal-6 case).
    gs = _state([DEFEND, STRIKE], [_enemy("E_0", hp=99, attack=1)], energy=1)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Strike"


def test_block_choice_uses_the_block_the_game_reports():
    """Dexterity arrives already applied to the card text.

    At +3 Dexterity the game reports Defend as "Gain 8 Block", so 8 alone
    covers the 7 incoming and the bigger Survivor is not needed. The bot must
    read that number rather than adding Dexterity on top of it.
    """
    # At +3 Dexterity the game reports BOTH cards already buffed: Defend
    # "Gain 8 Block", Survivor "Gain 11 Block". 8 alone covers the 7 incoming,
    # so the smaller sufficient card wins and the 11 is saved.
    buffed_defend = dict(DEFEND, description="Gain 8 Block.")
    buffed_survivor = dict(SURVIVOR, description="Gain 11 Block.")
    gs = _state(
        [buffed_survivor, buffed_defend],
        [_enemy("E_0", hp=99, attack=7)],
        player_hp=12,
        dexterity=3,
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


def test_effective_block_does_not_re_apply_dexterity():
    """The game bakes Dexterity into the text -- at -2 a Defend reads "Gain 3
    Block". Applying it again double-counted, and at -4 the bot computed 0 for
    a Defend that still granted 1, then played it anyway for "nothing"."""
    assert combat._effective_block(DEFEND, 0) == 5
    assert combat._effective_block(DEFEND, 3) == 5   # not 8: text already has it
    assert combat._effective_block(dict(DEFEND, description="Gain 8 Block."), 3) == 8
    assert combat._effective_block(STRIKE, 3) == 0   # not a block card
