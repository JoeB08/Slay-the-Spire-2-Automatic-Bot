"""Trading HP for tempo in long elite fights (note 22).

The bot was blocking chip damage every turn against a high-HP elite, taking
more total damage across a long grind than it would have by eating some hits
and killing sooner.
"""
import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "target_type": "Self",
          "description": "Gain 5 Block."}


def _state(state_type, enemy_hp, label, hp=70, energy=3, cards=None):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in (cards or [DEFEND, STRIKE])]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["state_type"] = state_type
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["hp"] = hp
    raw["player"]["max_hp"] = 70
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    # No potions: elite fights spend potions before playing cards, which would
    # mask the block-vs-attack decision under test.
    raw["player"]["potions"] = []
    raw["battle"]["enemies"] = [{
        "entity_id": "ELITE_0", "name": "Bygone Effigy", "hp": enemy_hp, "max_hp": enemy_hp,
        "block": 0, "status": [],
        "intents": [{"type": "Attack", "label": label, "title": "", "description": ""}],
    }]
    return GameState(raw)


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


def test_races_a_long_elite_fight_instead_of_turtling():
    # 120 HP elite chipping for 5 a turn: blocking every turn costs more total
    # HP than eating the chip and ending the fight sooner.
    gs = _state("elite", enemy_hp=120, label="5", hp=70)
    assert combat._should_race(gs, gs.enemies, combat._playable_hand(gs), 0) is True
    action, fields = combat.decide(gs)
    assert combat._card_damage(_played(gs, fields)) > 0


def test_does_not_race_in_an_ordinary_fight():
    gs = _state("monster", enemy_hp=120, label="5", hp=70)
    assert combat._should_race(gs, gs.enemies, combat._playable_hand(gs), 0) is False
    action, fields = combat.decide(gs)
    assert combat._card_block(_played(gs, fields)) > 0


def test_does_not_race_a_short_elite_fight():
    # Nearly dead already -- no grind to shorten, so block normally.
    gs = _state("elite", enemy_hp=8, label="5", hp=70)
    assert combat._should_race(gs, gs.enemies, combat._playable_hand(gs), 0) is False


def test_does_not_race_when_low_on_hp():
    # Racing while nearly dead is just dying faster.
    gs = _state("elite", enemy_hp=120, label="5", hp=14)
    assert combat._should_race(gs, gs.enemies, combat._playable_hand(gs), 0) is False


def test_still_blocks_a_big_hit_even_while_racing():
    # Race mode widens the chip tolerance, it does not switch off survival:
    # a hit that would drop us below the safety floor is still blocked.
    gs = _state("elite", enemy_hp=120, label="40", hp=45)
    action, fields = combat.decide(gs)
    assert combat._card_block(_played(gs, fields)) > 0
