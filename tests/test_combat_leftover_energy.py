import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

# A low-synergy skill that fails the value step's minimum-score bar, so
# nothing before the final scan will pick it up.
LOW_VALUE_SKILL = {"name": "Anticipate", "cost": "1", "description": "Gain 2 Dexterity this turn.", "target_type": "Self"}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}
UNPLAYABLE_CURSE = {
    "name": "Clumsy",
    "cost": "0",
    "type": "Curse",
    "description": "Unplayable.",
    "target_type": "None",
    "can_play": False,
}


def _state(cards, attack=None, energy=3, player_hp=70):
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
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = 999
    enemy["status"] = []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(attack), "title": "", "description": ""}]
        if attack
        else [{"type": "StatusCard", "label": "1", "title": "", "description": ""}]
    )
    return GameState(raw)


def test_plays_a_low_value_card_rather_than_ending_turn_with_energy():
    # Nothing is attacking (so no block step fires) and the card is below the
    # value step's score bar -- previously this ended the turn with 3 energy
    # and a playable card still in hand.
    gs = _state([LOW_VALUE_SKILL], attack=None, energy=3)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0


def test_still_ends_turn_when_nothing_is_playable():
    gs = _state([UNPLAYABLE_CURSE], attack=None, energy=3)
    action, _ = combat.decide(gs)
    assert action == "end_turn"


def test_ends_turn_when_out_of_energy():
    # A real payload marks an unaffordable card can_play=False; that -- not
    # our own cost arithmetic -- is what makes it unplayable.
    broke = dict(LOW_VALUE_SKILL, can_play=False, unplayable_reason="EnergyCostTooHigh")
    gs = _state([broke], attack=None, energy=0)
    action, _ = combat.decide(gs)
    assert action == "end_turn"


def test_plays_a_card_whose_cost_dropped_to_zero_even_at_no_energy():
    # Pounce / Bullet Time style cost reduction: the printed cost still reads
    # 1, but the game reports can_play=True at 0 energy. Trusting our own cost
    # math instead stranded these in hand.
    free_now = dict(LOW_VALUE_SKILL, can_play=True)
    gs = _state([free_now], attack=None, energy=0)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0


def test_leftover_scan_prefers_a_useful_card_over_block_that_would_decay():
    # Nothing attacking: a Defend would just decay, so spend the energy on the
    # card that actually does something instead.
    gs = _state([DEFEND, LOW_VALUE_SKILL], attack=None, energy=1)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Anticipate"
