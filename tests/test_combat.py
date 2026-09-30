import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture


def test_skips_block_for_genuine_chip_damage():
    # 1 incoming at full HP is chip damage -- take it and deal 6 instead.
    # (The fixture's own 4-damage intent is deliberately NOT chip at 70 max
    # HP: surviving the turn isn't sufficient reason to eat a real hit.)
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    raw["battle"]["enemies"][0]["intents"] = [{"type": "Attack", "label": "1", "title": "", "description": ""}]
    for e in raw["battle"]["enemies"][1:]:
        e["intents"] = []
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) > 0


def test_kills_a_killable_attacker_rather_than_blocking_it():
    # The fixture's attacker has 10 HP and we hold three 6-damage Strikes, so
    # it dies this turn -- removing 100% of its damage permanently, which
    # beats spending a card on block that's gone next turn.
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    raw["player"]["hp"] = 10
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) > 0
    assert fields["target"] == "TWIG_SLIME_S_0"


def test_blocks_when_the_attacker_is_too_big_to_kill():
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    raw["player"]["hp"] = 10
    raw["battle"]["enemies"][0]["hp"] = 500  # far out of reach this turn
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) > 0


def test_takes_lethal_over_everything_else():
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    # Bring the Twig Slime down to Strike-killable range and remove its threat
    # so the block branch shouldn't fire either.
    raw["battle"]["enemies"][0]["hp"] = 5
    raw["battle"]["enemies"][0]["intents"] = [{"type": "Debuff", "label": "", "title": "", "description": ""}]
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) >= 5
    assert fields.get("target") == "TWIG_SLIME_S_0"


def test_ends_turn_with_no_enemies_left():
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    for e in raw["battle"]["enemies"]:
        e["hp"] = 0
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "end_turn"


def test_targets_lowest_hp_enemy_when_attacking():
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    # Only leave attack cards in hand so block/value plays can't preempt the
    # damage branch, remove threats, and make sure no card would be lethal.
    raw["player"]["hand"] = [c for c in raw["player"]["hand"] if c["name"] == "Strike"]
    for e in raw["battle"]["enemies"]:
        e["intents"] = []
    raw["battle"]["enemies"][0]["hp"] = 999  # twig slime -- no longer the lowest
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    lowest = min(gs.enemies, key=lambda e: e["hp"])
    assert fields["target"] == lowest["entity_id"]
