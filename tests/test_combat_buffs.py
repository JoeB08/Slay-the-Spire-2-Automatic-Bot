import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

FOOTWORK = {"name": "Footwork", "cost": "1", "description": "Gain 2 Dexterity.", "target_type": "Self"}
ANTICIPATE = {"name": "Anticipate", "cost": "0", "description": "Gain 2 Dexterity this turn.", "target_type": "Self"}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
SHIV = {"name": "Shiv", "cost": "0", "description": "Deal 4 damage. Exhaust."}
WRAITH_FORM = {
    "name": "Wraith Form",
    "cost": "3",
    "description": "Gain 2 Intangible. At the start of your turn, lose 1 Dexterity.",
    "target_type": "Self",
}
MALAISE = {"name": "Malaise", "cost": "1", "description": "Enemy loses 2 Strength. Apply 2 Weak."}


def _state(cards, enemy_hp=999, incoming_label=None, player_hp=70, max_hp=70, energy=3):
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
    raw["player"]["block"] = 0
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["status"] = []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(incoming_label), "title": "", "description": ""}]
        if incoming_label is not None
        else [{"type": "StatusCard", "label": "1", "title": "", "description": ""}]
    )
    return raw


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


def test_dexterity_buff_played_before_defend():
    # Dexterity raises Block gained, so Footwork must precede the Defend it
    # is meant to pump -- otherwise the buff does nothing this turn.
    gs = GameState(_state([DEFEND, FOOTWORK], player_hp=20, incoming_label=10))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Footwork"


def test_buff_played_before_attacks():
    gs = GameState(_state([STRIKE, FOOTWORK], incoming_label=None))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Footwork"


def test_buff_played_before_free_damage_shivs():
    gs = GameState(_state([SHIV, FOOTWORK], incoming_label=None))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Footwork"


def test_lethal_still_beats_playing_a_buff():
    # If we can just win the exchange now, do that instead of scaling.
    gs = GameState(_state([STRIKE, FOOTWORK], enemy_hp=5, incoming_label=None))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Strike"


def test_buff_skipped_when_it_would_starve_the_block_we_need():
    # 1 energy, a lethal-ish hit incoming, and only enough energy for one
    # card -- survival beats scaling.
    gs = GameState(_state([DEFEND, FOOTWORK], player_hp=6, incoming_label=5, energy=1))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


def test_buff_still_played_under_pressure_if_energy_covers_both():
    # 3 energy: Footwork (1) still leaves enough for the Defend (1) that
    # covers the hit, so scale first.
    gs = GameState(_state([DEFEND, FOOTWORK], player_hp=6, incoming_label=5, energy=3))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Footwork"


def test_zero_cost_temporary_dexterity_also_counts_as_a_buff():
    gs = GameState(_state([DEFEND, ANTICIPATE], player_hp=20, incoming_label=10))
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Anticipate"


def test_dexterity_loss_card_is_not_treated_as_a_buff():
    # Wraith Form *loses* Dexterity -- must not be picked up by the buff rule.
    assert combat._is_scaling_buff(WRAITH_FORM) is False


def test_enemy_strength_debuff_is_not_treated_as_a_self_buff():
    # Malaise removes enemy Strength; it's a debuff, not a scaling buff.
    assert combat._is_scaling_buff(MALAISE) is False
