"""The sequencing step: cards worth less if deferred are played first.

Four separate bugs were the same defect -- `_play_timing` ranked a card
correctly, but an earlier step in `decide()` committed the turn before
ordering was ever consulted. Each was patched in its own step until the
guards were consolidated into one rule. These tests pin that rule, and
especially the survival guard, which must always win.
"""
from __future__ import annotations

import pytest

from bot.game_state import GameState
from bot.strategy import combat


def _card(**kw):
    kw.setdefault("can_play", True)
    kw.setdefault("target_type", "Self")
    return kw


FLECHETTES = _card(name="Flechettes", cost="1", type="Attack",
                   description="Deal 5 damage for each Skill in your Hand.",
                   target_type="AnyEnemy")
CHOKE = _card(name="Choke", cost="1", type="Skill",
              description="Whenever you play a card this turn, the enemy loses 3 HP.",
              target_type="AnyEnemy")
SHIV = _card(name="Shiv", cost="0", type="Attack", description="Deal 4 damage.",
             target_type="AnyEnemy")
SURVIVOR = _card(name="Survivor", cost="1", type="Skill",
                 description="Gain 8 Block. Discard 1 card.")
DEFEND = _card(name="Defend", cost="1", type="Skill", description="Gain 5 Block.")
GAMBLE = _card(name="Calculated Gamble", cost="0", type="Skill",
               description="Discard your hand, then draw that many cards.")
INFECTION = _card(name="Infection", cost="1", type="Status", can_play=False,
                  description="Unplayable. At the end of your turn, if this is "
                              "in your Hand, take 3 damage.")


def _state(hand, hp=48, energy=3, incoming=12, enemy_hp=38):
    cards = [dict(c, index=i) for i, c in enumerate(hand)]
    return GameState({
        "state_type": "monster",
        "run": {"act": 1, "floor": 8, "ascension": 0},
        "player": {"hp": hp, "max_hp": 70, "energy": energy, "block": 0,
                   "status": [], "potions": [], "hand": cards, "draw_pile": [],
                   "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{
            "entity_id": "E0", "name": "Wriggler", "hp": enemy_hp, "max_hp": 40,
            "block": 0, "status": [],
            "intents": [{"type": "Attack", "label": str(incoming),
                         "title": "", "description": ""}]}]},
    })


def _played(gs):
    action, fields = combat.decide(gs)
    if action != "play_card":
        return action
    return next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])


def test_flechettes_goes_before_a_block_skill():
    """Survivor is a Skill, so blocking first shrinks Flechettes by 5."""
    assert _played(_state([FLECHETTES, DEFEND, DEFEND, SURVIVOR, DEFEND])) == "Flechettes"


def test_choke_goes_before_a_free_shiv():
    """Choke collects from every card played after it."""
    assert _played(_state([CHOKE, SHIV, SHIV, DEFEND], incoming=0)) == "Choke"


def test_a_free_shiv_is_still_dumped_when_nothing_collects():
    assert _played(_state([SHIV, SHIV, DEFEND], incoming=0)) == "Shiv"


def test_a_clog_outlet_beats_a_defend():
    """Three Infections cost 9 a turn; a Defend blocks 5 once."""
    hand = [GAMBLE, DEFEND, INFECTION, dict(INFECTION), dict(INFECTION)]
    assert _played(_state(hand, incoming=6)) == "Calculated Gamble"


def test_one_dead_card_is_not_a_clog():
    assert _played(_state([GAMBLE, DEFEND, INFECTION], incoming=6)) != "Calculated Gamble"


@pytest.mark.parametrize("hand,hp,energy", [
    ([FLECHETTES, DEFEND, SURVIVOR], 9, 1),
    ([GAMBLE, DEFEND, INFECTION, dict(INFECTION)], 7, 1),
])
def test_survival_always_wins_the_tie(hand, hp, energy):
    """Never reorder past a block we need to live through the turn."""
    assert _played(_state(hand, hp=hp, energy=energy, incoming=12)) in ("Defend", "Survivor")
