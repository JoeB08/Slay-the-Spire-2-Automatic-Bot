import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture


def _hand_with(raw, cards):
    raw = copy.deepcopy(raw)
    for i, c in enumerate(cards):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = cards
    raw["player"]["energy"] = 3
    raw["player"]["max_energy"] = 3
    for e in raw["battle"]["enemies"]:
        e["intents"] = []  # no incoming threat -- isolate the damage-priority logic
    return raw


LEADING_STRIKE = {
    "name": "Leading Strike",
    "cost": "1",
    "description": "Deal 3 damage. Add 2 Shivs into your Hand.",
}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
ASSASSINATE = {
    "name": "Assassinate",
    "cost": "0",
    "description": "Innate. Deal 10 damage. Apply 1 Vulnerable. Exhaust.",
}


def test_shiv_generation_counts_toward_total_damage_for_lethal():
    raw = _hand_with(load_fixture("combat_turn1.json"), [copy.deepcopy(LEADING_STRIKE)])
    # Leading Strike alone deals 3, but 3 + 2*4 (shivs) = 11 clears this HP,
    # which a naive "Deal 3 damage" reading would have missed entirely.
    raw["battle"]["enemies"][0]["hp"] = 10
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0
    assert fields["target"] == raw["battle"]["enemies"][0]["entity_id"]


def test_leading_strike_beats_plain_strike_once_shivs_are_counted():
    raw = _hand_with(load_fixture("combat_turn1.json"), [copy.deepcopy(STRIKE), copy.deepcopy(LEADING_STRIKE)])
    raw["battle"]["enemies"][0]["hp"] = 999  # keep it out of lethal range
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    # Strike is index 0 (6 dmg face value); Leading Strike is index 1 but
    # worth 3 + 2*4 = 11 once its shivs are counted, so it should win despite
    # its lower face-value damage.
    assert fields["card_index"] == 1


def test_vulnerable_attack_sequenced_before_plain_attack_on_undebuffed_target():
    raw = _hand_with(load_fixture("combat_turn1.json"), [copy.deepcopy(STRIKE), copy.deepcopy(ASSASSINATE)])
    raw["battle"]["enemies"][0]["hp"] = 999
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    # Assassinate (index 1) applies Vulnerable and should go first even though
    # its own hit isn't overwhelmingly bigger, so later attacks benefit.
    assert fields["card_index"] == 1


def test_free_damage_card_played_ahead_of_paid_value_card():
    # A 0-cost Shiv should never sit unplayed behind a paid non-attack "value"
    # card -- it costs nothing, so there's no reason to defer it.
    shiv = {"name": "Shiv", "cost": "0", "description": "Deal 4 damage. Exhaust."}
    adrenaline = {"name": "Adrenaline", "cost": "0", "description": "Gain 1 Energy. Draw 2 cards. Exhaust."}
    raw = _hand_with(load_fixture("combat_turn1.json"), [copy.deepcopy(adrenaline), copy.deepcopy(shiv)])
    raw["battle"]["enemies"][0]["hp"] = 999
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 1  # the Shiv, not Adrenaline


def test_free_damage_still_prefers_vulnerable_setup_first():
    shiv = {"name": "Shiv", "cost": "0", "description": "Deal 4 damage. Exhaust."}
    assassinate_free = {
        "name": "Assassinate",
        "cost": "0",
        "description": "Innate. Deal 10 damage. Apply 1 Vulnerable. Exhaust.",
    }
    raw = _hand_with(load_fixture("combat_turn1.json"), [copy.deepcopy(shiv), copy.deepcopy(assassinate_free)])
    raw["battle"]["enemies"][0]["hp"] = 999
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 1  # Assassinate: sets up Vulnerable for what follows


def test_vulnerable_multiplier_applied_when_target_already_debuffed():
    raw = _hand_with(load_fixture("combat_turn1.json"), [copy.deepcopy(STRIKE)])
    raw["battle"]["enemies"][0]["hp"] = 8
    raw["battle"]["enemies"][0]["status"] = [{"id": "VULNERABLE_POWER", "name": "Vulnerable", "amount": 1}]
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    # 6 damage * 1.5 Vulnerable = 9, which clears 8 HP -- a naive non-Vulnerable
    # read (6 < 8) would have wrongly skipped the lethal branch.
    assert action == "play_card"
    assert fields["card_index"] == 0
