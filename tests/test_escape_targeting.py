"""An enemy about to escape is the last chance to kill it.

`Fat Gremlin` carries `type: Escape` ("This enemy intends to Escape"). It deals
no damage, so every "is it attacking?" filter skipped it, and the lethal step --
which sorts by how easy each enemy is to finish -- would happily kill the one
that was going to stay put anyway.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat

STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "can_play": True,
          "description": "Deal 6 damage.", "target_type": "AnyEnemy"}


def _enemy(eid, hp, escaping=False):
    intents = ([{"type": "Escape", "label": "", "title": "", "description": "This enemy intends to Escape."}]
               if escaping else
               [{"type": "Attack", "label": "8", "title": "", "description": ""}])
    return {"entity_id": eid, "name": "Fat Gremlin" if escaping else "Gremlin",
            "hp": hp, "max_hp": 20, "block": 0, "status": [], "intents": intents}


def _state(enemies, cards=(STRIKE,)):
    hand = [dict(c, index=i) for i, c in enumerate(copy.deepcopy(list(cards)))]
    return GameState({
        "state_type": "monster", "run": {"act": 1, "floor": 6, "ascension": 0},
        "player": {"hp": 60, "max_hp": 70, "energy": 3, "block": 0, "status": [], "potions": [],
                   "hand": hand, "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": enemies},
    })


def test_escape_intent_is_recognised():
    assert combat._is_escaping(_enemy("A", 5, escaping=True)) is True
    assert combat._is_escaping(_enemy("B", 5)) is False


def test_lethal_prefers_the_escaping_enemy():
    gs = _state([_enemy("STAY", 5), _enemy("FLEE", 5, escaping=True)])
    _, fields = combat.decide(gs)
    assert fields.get("target") == "FLEE"


def test_an_easier_kill_does_not_outrank_the_escaping_one():
    # The stayer is easier to finish, but it will still be there next turn.
    gs = _state([_enemy("STAY", 2), _enemy("FLEE", 6, escaping=True)])
    _, fields = combat.decide(gs)
    assert fields.get("target") == "FLEE"


def test_an_escaping_enemy_out_of_reach_is_not_chased():
    # 30 HP with a single 6-damage Strike: no lethal, so normal targeting.
    gs = _state([_enemy("STAY", 4), _enemy("FLEE", 30, escaping=True)])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields.get("target") == "STAY"
