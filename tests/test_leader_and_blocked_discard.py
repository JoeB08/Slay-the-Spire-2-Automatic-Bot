import copy

from bot.game_state import GameState
from bot.strategy import combat, misc_screens
from conftest import load_fixture

STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}


def _enemy(entity_id, hp, attack=None, name=None, status=None, intent_desc=""):
    return {
        "entity_id": entity_id,
        "name": name or entity_id,
        "hp": hp,
        "max_hp": 99,
        "block": 0,
        "status": status or [],
        "intents": (
            [{"type": "Attack", "label": str(attack), "title": "", "description": intent_desc}]
            if attack
            else [{"type": "StatusCard", "label": "1", "title": "", "description": intent_desc}]
        ),
    }


# --- leader detection ---

def test_detects_leader_by_name():
    assert combat._is_likely_leader(_enemy("X", 10, name="Gremlin Leader")) is True
    assert combat._is_likely_leader(_enemy("SLIME_0", 10, name="Twig Slime")) is False


def test_detects_leader_by_summoning_behaviour():
    summoner = _enemy("X", 10, name="Nest", intent_desc="This enemy intends to summon reinforcements.")
    assert combat._is_likely_leader(summoner) is True


def test_kills_the_leader_even_when_other_damage_would_otherwise_deter_it():
    # Killing the leader ends the fight, so the huge incoming swing from the
    # other enemy never lands -- the usual "would the rest kill me?" guard
    # must not block this.
    gs = _state(
        [STRIKE, STRIKE, DEFEND],
        [
            _enemy("LEADER_0", hp=10, attack=3, name="Gremlin Leader"),
            _enemy("MINION_0", hp=99, attack=40),
        ],
        hp=12,
    )
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_damage(played) > 0
    assert fields["target"] == "LEADER_0"


def test_ordinary_attacker_still_respects_the_safety_guard():
    # Same shape, but no leader -- chasing the small kill would leave the
    # 40-damage swing to finish us, so block instead.
    gs = _state(
        [STRIKE, STRIKE, DEFEND],
        [_enemy("SMALL_0", hp=10, attack=3), _enemy("HUGE_0", hp=99, attack=40)],
        hp=12,
    )
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) > 0


def _state(cards, enemies, hp=70, energy=3, block=0):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["hp"] = hp
    raw["player"]["max_hp"] = 70
    raw["player"]["block"] = block
    raw["player"]["status"] = []
    # No potions: a lethal-threat fixture would otherwise trigger the
    # last-ditch potion use and mask the card decision under test.
    raw["player"]["potions"] = []
    raw["battle"]["enemies"] = enemies
    return GameState(raw)


# --- discarding while already fully blocked ---

def _discard_state(cards, enemies, block):
    raw = copy.deepcopy(load_fixture("hand_select_discard.json"))
    offered = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(offered):
        c["index"] = i
        c.setdefault("can_play", True)
    raw["hand_select"] = {
        "mode": "simple_select",
        "prompt": "Choose a card to Discard.",
        "cards": offered,
        "selected_cards": [],
        "can_confirm": False,
    }
    raw["player"]["hand"] = offered
    raw["player"]["block"] = block
    raw["player"]["energy"] = 3
    raw["player"]["status"] = []
    raw["battle"]["enemies"] = enemies
    return GameState(raw)


def _chosen(gs, fields):
    return next(c for c in gs.raw["hand_select"]["cards"] if c["index"] == fields["card_index"])


def test_discards_surplus_block_when_already_fully_covered():
    # 8 incoming, 20 block already -- another Defend does nothing this turn,
    # so pitch it and keep the Strike to spend energy on.
    gs = _discard_state([DEFEND, STRIKE], [_enemy("E_0", hp=99, attack=8)], block=20)
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Defend"


def test_keeps_block_card_when_damage_is_still_incoming():
    gs = _discard_state([DEFEND, STRIKE], [_enemy("E_0", hp=99, attack=20)], block=0)
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] != "Defend"
