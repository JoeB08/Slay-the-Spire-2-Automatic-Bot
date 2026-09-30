import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}

PLATING = {"id": "PLATING_POWER", "name": "Plating", "amount": 8, "type": "Buff",
           "description": "At the end of your turn, gain 8 Block."}
INTANGIBLE = {"id": "INTANGIBLE_POWER", "name": "Intangible", "amount": 2, "type": "Buff",
              "description": "Reduce ALL damage taken to 1."}


def _state(cards, statuses=None, attack=None, hp=30, max_hp=70, block=0, energy=3, enemy_hp=999):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["hp"] = hp
    raw["player"]["max_hp"] = max_hp
    raw["player"]["block"] = block
    raw["player"]["status"] = statuses or []
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["status"] = []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(attack), "title": "", "description": ""}] if attack else []
    )
    return GameState(raw)


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


def test_plating_counts_toward_block_and_prevents_overblocking():
    # 8 incoming, and Plating already grants 8 Block for free -- no need to
    # spend a Defend on it, so attack instead.
    gs = _state([DEFEND, STRIKE], statuses=[dict(PLATING)], attack=8)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert combat._card_damage(_played(gs, fields)) > 0


def test_still_blocks_when_plating_does_not_cover_the_hit():
    gs = _state([DEFEND, STRIKE], statuses=[dict(PLATING)], attack=25, hp=20)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert combat._card_block(_played(gs, fields)) > 0


def test_intangible_reduces_incoming_to_one_per_hit():
    # A 40-damage swing lands for 1 under Intangible -- blocking it is waste.
    gs = _state([DEFEND, STRIKE], statuses=[dict(INTANGIBLE)], attack=40, hp=25)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert combat._card_damage(_played(gs, fields)) > 0


def test_pending_block_helper_reads_amount_and_effect_text():
    gs = _state([], statuses=[dict(PLATING)])
    assert combat._pending_block_from_status(gs) == 8

    generic = {"id": "SOMETHING", "name": "Something", "amount": 4,
               "description": "At the start of your turn, gain 4 Block."}
    gs2 = _state([], statuses=[generic])
    assert combat._pending_block_from_status(gs2) == 4


def test_unrelated_status_does_not_count_as_block():
    strength = {"id": "STRENGTH_POWER", "name": "Strength", "amount": 3,
                "description": "Strength adds additional damage to Attacks."}
    gs = _state([], statuses=[strength])
    assert combat._pending_block_from_status(gs) == 0
