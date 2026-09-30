import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block."}
SURVIVOR = {"name": "Survivor", "cost": "1", "description": "Gain 8 Block."}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}


def _state(cards, enemy_hp=999, enemy_poison=0, incoming_label=None, player_hp=70, max_hp=70, hand_extra=None):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards] + [copy.deepcopy(c) for c in (hand_extra or [])]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = 3
    raw["player"]["max_energy"] = 3
    raw["player"]["hp"] = player_hp
    raw["player"]["max_hp"] = max_hp
    raw["player"]["block"] = 0
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["status"] = [{"id": "POISON_POWER", "name": "Poison", "amount": enemy_poison}] if enemy_poison else []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(incoming_label), "title": "", "description": ""}]
        if incoming_label is not None
        else [{"type": "StatusCard", "label": "1", "title": "", "description": ""}]
    )
    return raw


def test_no_block_when_nothing_is_attacking():
    # Enemy's only intent is StatusCard (no Attack) -- even at low HP there's
    # nothing to block against, so it should attack instead of blocking.
    raw = _state([DEFEND, STRIKE], player_hp=5, incoming_label=None)
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) > 0


def test_skips_block_when_taking_the_hit_is_affordable():
    # High HP, small incoming hit -- take 1 damage, deal 6, rather than block.
    raw = _state([DEFEND, STRIKE], player_hp=70, incoming_label=1)
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) > 0


def test_blocks_when_the_hit_would_cross_the_safety_floor():
    raw = _state([DEFEND, STRIKE], player_hp=6, incoming_label=4)
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) > 0


def test_picks_smallest_sufficient_block_card_not_the_biggest():
    # Both Defend (5) and Survivor (8) cover a 4-damage gap -- prefer Defend.
    raw = _state([DEFEND, SURVIVOR], player_hp=6, incoming_label=4)
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Defend"


def test_does_not_overshoot_with_multiple_small_blocks_once_covered():
    # 8 block from one Survivor already covers 4 incoming -- must_block should
    # be false on the *next* decision now that current_block >= incoming.
    raw = _state([DEFEND, STRIKE], player_hp=6, incoming_label=4)
    raw["player"]["block"] = 8
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) > 0  # not another block card


def test_poison_alone_lets_lethal_fire_below_face_value_damage():
    # 6 damage alone wouldn't kill 10 HP, but 4 Poison already ticking down
    # covers the rest -- this should register as lethal.
    raw = _state([STRIKE], enemy_hp=10, enemy_poison=4, incoming_label=None)
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0


def test_enemy_doomed_by_poison_is_not_targeted_when_another_enemy_needs_damage():
    raw = _state([STRIKE], enemy_hp=999, incoming_label=None)
    doomed = copy.deepcopy(raw["battle"]["enemies"][0])
    doomed["entity_id"] = "DOOMED_0"
    doomed["hp"] = 3
    doomed["status"] = [{"id": "POISON_POWER", "name": "Poison", "amount": 5}]
    doomed["intents"] = [{"type": "Attack", "label": "20", "title": "", "description": ""}]
    raw["battle"]["enemies"].append(doomed)
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    # Should target the real threat, not waste a card finishing the doomed one.
    assert fields.get("target") != "DOOMED_0"


def test_curse_in_hand_damage_forces_block_even_with_no_attacking_enemy():
    curse = {
        "name": "Decay",
        "cost": "1",
        "type": "Curse",
        "description": "At the end of your turn, take 5 damage.",
        "can_play": False,
    }
    raw = _state([DEFEND, STRIKE], player_hp=6, incoming_label=None, hand_extra=[curse])
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) > 0
