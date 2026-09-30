from bot.game_state import GameState
from bot.strategy import potions as potion_db

FIRE = {"id": "FIRE_POTION", "name": "Fire Potion", "description": "Deal 20 damage.", "slot": 0,
        "can_use_in_combat": True, "target_type": "AnyEnemy"}
BLOCK_POTION = {"id": "BLOCK_POTION", "name": "Block Potion", "description": "Gain 12 Block.", "slot": 0,
                "can_use_in_combat": True, "target_type": "AnyPlayer"}
HEAL = {"id": "HEAL_POTION", "name": "Blood Potion", "description": "Heal 10 HP.", "slot": 0,
        "can_use_in_combat": True, "target_type": "AnyPlayer"}
SWIFT = {"id": "SWIFT_POTION", "name": "Swift Potion", "description": "Draw 3 cards.", "slot": 0,
         "can_use_in_combat": True, "target_type": "AnyPlayer"}


def _state(potions, state_type="monster", hp=70, max_hp=70, enemy_hp=80, attack=None, block=0, max_slots=3):
    for i, p in enumerate(potions):
        p["slot"] = i
    return GameState(
        {
            "state_type": state_type,
            "run": {"act": 1, "floor": 5},
            "battle": {
                "is_play_phase": True,
                "enemies": [
                    {
                        "entity_id": "E_0", "name": "Foe", "hp": enemy_hp, "max_hp": 80, "block": 0, "status": [],
                        "intents": ([{"type": "Attack", "label": str(attack), "title": "", "description": ""}]
                                    if attack else []),
                    }
                ],
            },
            "player": {
                "hp": hp, "max_hp": max_hp, "block": block, "gold": 0, "energy": 3, "max_energy": 3,
                "status": [], "potions": potions, "max_potion_slots": max_slots,
                "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def test_spends_offensive_potion_freely_in_an_elite_fight():
    gs = _state([dict(FIRE)], state_type="elite")
    result = potion_db.suggest_potion_use(gs)
    assert result is not None
    assert result[0] == "use_potion"


def test_spends_offensive_potion_freely_in_a_boss_fight():
    gs = _state([dict(FIRE)], state_type="boss")
    assert potion_db.suggest_potion_use(gs) is not None


def test_holds_offensive_potion_in_a_trash_fight_at_full_hp():
    gs = _state([dict(FIRE)], state_type="monster", hp=70, enemy_hp=80)
    assert potion_db.suggest_potion_use(gs) is None


def test_uses_a_potion_to_seal_a_kill_even_in_a_trash_fight():
    gs = _state([dict(FIRE)], state_type="monster", enemy_hp=15)
    result = potion_db.suggest_potion_use(gs)
    assert result is not None


def test_blocks_with_a_potion_when_about_to_die():
    gs = _state([dict(BLOCK_POTION)], state_type="monster", hp=10, attack=25)
    result = potion_db.suggest_potion_use(gs)
    assert result is not None
    assert result[1]["slot"] == 0


def test_heals_when_critically_low():
    gs = _state([dict(HEAL)], state_type="monster", hp=12, max_hp=70)
    result = potion_db.suggest_potion_use(gs)
    assert result is not None


def test_uses_defensive_potion_when_low_hp_in_a_normal_fight():
    gs = _state([dict(BLOCK_POTION)], state_type="monster", hp=25, max_hp=70, attack=12)
    assert potion_db.suggest_potion_use(gs) is not None


def test_does_not_burn_a_utility_potion_just_for_being_low_in_a_trash_fight():
    # Drawing cards is not an answer to low HP -- save it for an elite.
    gs = _state([dict(SWIFT)], state_type="monster", hp=25, max_hp=70, attack=5)
    assert potion_db.suggest_potion_use(gs) is None


def test_uses_utility_potion_in_an_elite_fight():
    gs = _state([dict(SWIFT)], state_type="elite", hp=60, max_hp=70)
    assert potion_db.suggest_potion_use(gs) is not None


def test_cashes_in_a_potion_when_slots_are_full():
    gs = _state([dict(FIRE), dict(HEAL), dict(SWIFT)], state_type="monster", hp=70, max_slots=3)
    assert potion_db.suggest_potion_use(gs) is not None


def test_potion_value_ranks_by_effect_and_damage_size():
    assert potion_db.potion_value(FIRE) > potion_db.potion_value(SWIFT)
    big = {"description": "Deal 30 damage."}
    small = {"description": "Deal 8 damage."}
    assert potion_db.potion_value(big) > potion_db.potion_value(small)


def test_potion_value_reads_the_reward_screen_field_name():
    # Reward items expose `potion_description`, not `description`.
    assert potion_db.potion_value({"potion_description": "Heal 10 HP."}) > 0


def test_unknown_potion_still_has_a_value():
    assert potion_db.potion_value({"description": "Does something strange."}) > 0


def test_ignores_potions_that_cannot_be_used_in_combat():
    unusable = dict(FIRE)
    unusable["can_use_in_combat"] = False
    gs = _state([unusable], state_type="elite")
    assert potion_db.suggest_potion_use(gs) is None
