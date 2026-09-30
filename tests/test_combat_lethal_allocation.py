import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
BACKSTAB = {"name": "Backstab", "cost": "0", "description": "Innate. Deal 11 damage. Exhaust."}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}


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


def _state(cards, enemies, hp=70, max_hp=70, energy=3):
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
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    # No potions: a lethal-threat fixture would otherwise trigger the
    # last-ditch potion use and mask the card decision under test.
    raw["player"]["potions"] = []
    raw["battle"]["enemies"] = enemies
    return GameState(raw)


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


def test_uses_the_smallest_sufficient_kill_not_the_biggest():
    # Both cards kill the 5 HP enemy, but only Backstab (11) also kills the
    # 10 HP one. Spending Backstab on the small target strands the Strike.
    gs = _state(
        [STRIKE, BACKSTAB],
        [_enemy("SMALL_0", hp=5), _enemy("BIG_0", hp=10)],
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["target"] == "SMALL_0"
    assert _played(gs, fields)["name"] == "Strike"  # least overkill


def test_saved_big_card_then_kills_the_second_enemy():
    # Follow-up state after the small enemy died: Backstab handles the 10 HP one.
    gs = _state([BACKSTAB], [_enemy("BIG_0", hp=10)])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["target"] == "BIG_0"


def test_kills_the_attacker_across_two_cards_instead_of_blocking():
    # No single card kills the 10 HP attacker, but two Strikes do. Killing it
    # removes all 8 of its damage; blocking only postpones it.
    gs = _state(
        [STRIKE, STRIKE, DEFEND],
        [_enemy("ATTACKER_0", hp=10, attack=8), _enemy("IDLE_0", hp=40)],
        hp=20,
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = _played(gs, fields)
    assert combat._card_damage(played) > 0
    assert fields["target"] == "ATTACKER_0"


def test_blocks_instead_when_the_kill_still_leaves_us_dead():
    # Killing the small attacker is possible, but the other enemy's hit alone
    # would still finish us -- defend rather than chase the kill.
    gs = _state(
        [STRIKE, STRIKE, DEFEND],
        [_enemy("SMALL_0", hp=10, attack=3), _enemy("HUGE_0", hp=99, attack=40)],
        hp=12,
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert combat._card_block(_played(gs, fields)) > 0


def test_reachable_damage_accounts_for_energy_budget():
    enemy = _enemy("E_0", hp=99)
    hand = [dict(STRIKE, index=0), dict(STRIKE, index=1), dict(STRIKE, index=2)]
    # 3 energy affords all three 6-damage Strikes.
    assert combat._reachable_damage(hand, enemy, energy=3, shiv_bonus=0) == 18
    # 1 energy affords only one.
    assert combat._reachable_damage(hand, enemy, energy=1, shiv_bonus=0) == 6
