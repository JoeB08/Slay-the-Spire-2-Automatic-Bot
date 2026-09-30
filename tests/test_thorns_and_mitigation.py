"""Two per-hit mechanics the bot was blind to.

Thorns bills us once per hit we land; Strength reduction saves us once per hit
we take. Both scale with hit count, and the Silent's kit is built on hit count,
so ignoring them mis-prices exactly the cards the bot plays most.
"""
from bot.strategy import combat

THORNS = {"name": "Thorns", "amount": 2,
          "description": "When hit by an attack, deal 2 damage back."}
WAIL = {"name": "Piercing Wail", "cost": "1",
        "description": "ALL enemies lose 6 Strength this turn. Exhaust."}
BLADE_DANCE = {"name": "Blade Dance", "cost": "1",
               "description": "Add 3 Shivs into your Hand.", "target_type": "AnyEnemy"}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage.", "target_type": "AnyEnemy"}


def _enemy(label="10", thorns=False, hp=40):
    return {"entity_id": "E_0", "name": "E", "hp": hp, "max_hp": 40, "block": 0,
            "status": [dict(THORNS)] if thorns else [],
            "intents": [{"type": "Attack", "label": label, "title": "", "description": ""}]}


# --- hit counting ---------------------------------------------------------

def test_intent_labels_are_damage_by_hits():
    """"5x3" is 5 damage three times -- confirmed against the intent prose
    ("Attack for 5 damage 3 times"). Reading the first number as the hit count
    inverts every per-hit calculation."""
    assert combat._enemy_hit_count(_enemy("5x3")) == 3
    assert combat._enemy_hit_count(_enemy("12")) == 1
    assert combat._enemy_attack_damage(_enemy("5x3")) == 15


# --- thorns ---------------------------------------------------------------

def test_thorns_is_charged_once_per_hit_not_per_card():
    # Blade Dance makes 3 Shivs; `_hit_count` already folds those in, so the
    # cost is 3 x 2, not 6 x 2.
    assert combat._thorns_cost(BLADE_DANCE, _enemy(thorns=True)) == 6
    assert combat._thorns_cost(STRIKE, _enemy(thorns=True)) == 2
    assert combat._thorns_cost(STRIKE, _enemy(thorns=False)) == 0


def test_thorns_narrows_the_gap_between_multi_hit_and_single_hit():
    clean, spiky = _enemy(), _enemy(thorns=True)
    gap_clean = (combat._ranking_damage(BLADE_DANCE, clean, 0, None, 3.0)
                 - combat._ranking_damage(STRIKE, clean, 0, None, 3.0))
    gap_spiky = (combat._ranking_damage(BLADE_DANCE, spiky, 0, None, 3.0)
                 - combat._ranking_damage(STRIKE, spiky, 0, None, 3.0))
    assert gap_spiky < gap_clean


# --- mitigation -----------------------------------------------------------

def test_strength_reduction_scales_with_the_number_of_hits():
    """Piercing Wail fully covers a 5x3 (15) exactly as it covers a single 5,
    because -6 Strength applies to every hit. The bot previously treated
    `_reduces_incoming_damage` as a yes/no flag and never computed this."""
    assert combat._strength_down_amount(WAIL) == 6
    assert combat._mitigation_value(WAIL, [_enemy("12")]) == 6
    assert combat._mitigation_value(WAIL, [_enemy("5x3")]) == 15   # fully covered
    assert combat._mitigation_value(WAIL, [_enemy("15x3")]) == 18  # 6 x 3 hits


def test_mitigation_is_worth_nothing_against_an_enemy_that_is_not_attacking():
    idle = {"entity_id": "E_0", "name": "E", "hp": 40, "block": 0, "status": [],
            "intents": [{"type": "Buff", "label": "", "description": ""}]}
    assert combat._mitigation_value(WAIL, [idle]) == 0
