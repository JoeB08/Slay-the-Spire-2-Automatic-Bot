import copy

from bot.game_state import GameState
from bot.strategy import cards as card_db
from bot.strategy import combat, misc_screens
from conftest import load_fixture

SLY_ATTACK = {
    "name": "Flick-Flack",
    "cost": "1",
    "description": "Sly. Deal 6 damage to ALL enemies.",
    "keywords": [{"name": "Sly", "description": "If this card is discarded from your Hand before the end of your turn, play it for free."}],
    "target_type": "AllEnemies",
}
PLAIN_ATTACK = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}
SLIMED = {"name": "Slimed", "cost": "1", "type": "Status", "description": "Draw 1 card. Exhaust."}
GOOD_CARD = {"name": "Adrenaline", "cost": "0", "description": "Gain 1 Energy. Draw 2 cards. Exhaust."}
EXPENSIVE = {"name": "Tactician", "cost": "3", "description": "Gain 1 Energy."}


def test_detects_sly_from_keywords():
    assert card_db.is_sly(SLY_ATTACK) is True
    assert card_db.is_sly(PLAIN_ATTACK) is False


def test_detects_sly_from_description_prefix():
    assert card_db.is_sly({"name": "X", "description": "Sly. Gain 6 Block."}) is True


def test_detects_sly_from_static_card_data_by_name_alone():
    # Payloads that carry only a name (e.g. deck lists) still resolve.
    assert card_db.is_sly({"name": "Tactician"}) is True


def _combat_state(cards, enemy_hp=999, attack=None, energy=3):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["player"]["hand"] = hand
    raw["player"]["energy"] = energy
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["status"] = []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(attack), "title": "", "description": ""}] if attack else []
    )
    return GameState(raw)


def test_prefers_a_non_sly_attack_over_a_sly_one():
    # Paying energy for a Sly card throws away its keyword.
    gs = _combat_state([SLY_ATTACK, PLAIN_ATTACK])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Strike"


def test_still_plays_a_sly_card_when_it_is_the_only_option():
    gs = _combat_state([SLY_ATTACK])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields["card_index"] == 0


def _discard_state(cards, prompt="Choose a card to Discard.", energy=1):
    raw = copy.deepcopy(load_fixture("hand_select_discard.json"))
    offered = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(offered):
        c["index"] = i
    raw["hand_select"] = {
        "mode": "simple_select",
        "prompt": prompt,
        "cards": offered,
        "selected_cards": [],
        "can_confirm": False,
    }
    raw["player"]["energy"] = energy
    raw["player"]["hand"] = offered
    return GameState(raw)


def _chosen(gs, fields):
    return next(c for c in gs.raw["hand_select"]["cards"] if c["index"] == fields["card_index"])


def test_discards_a_sly_card_first_since_that_plays_it_free():
    gs = _discard_state([GOOD_CARD, SLIMED, SLY_ATTACK])
    action, fields = misc_screens.decide_hand_select(gs)
    assert action == "combat_select_card"
    assert _chosen(gs, fields)["name"] == "Flick-Flack"


def test_discards_dead_weight_when_no_sly_card_is_present():
    gs = _discard_state([GOOD_CARD, SLIMED, PLAIN_ATTACK])
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Slimed"


def test_discards_an_unaffordable_card_over_a_playable_one():
    gs = _discard_state([PLAIN_ATTACK, EXPENSIVE], energy=1)
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Tactician"


def test_keeps_a_playable_attack_over_a_skill_when_discarding():
    # Attacks convert to damage this turn -- pitch the skill instead.
    skill = {"name": "Deflect", "cost": "0", "description": "Gain 4 Block."}
    gs = _discard_state([PLAIN_ATTACK, skill], energy=3)
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] != "Strike"


def test_never_discards_an_attack_that_is_currently_lethal():
    gs = _discard_state([PLAIN_ATTACK, GOOD_CARD], energy=3)
    # Drop the enemy into Strike range so the attack is the kill.
    gs.raw["battle"]["enemies"] = [
        {"entity_id": "E_0", "name": "Foe", "hp": 5, "max_hp": 20, "block": 0, "status": [], "intents": []}
    ]
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] != "Strike"


ETHEREAL_CURSE = {
    "name": "Ethereal Curse",
    "cost": "0",
    "type": "Curse",
    "description": "Unplayable. Ethereal.",
    "can_play": False,
    "keywords": [{"name": "Ethereal", "description": "If this card is in your hand at the end of your turn, it is Exhausted."}],
}
PLAIN_CURSE = {"name": "Regret", "cost": "0", "type": "Curse", "description": "Unplayable.", "can_play": False}


def test_detects_ethereal():
    assert misc_screens._is_ethereal(ETHEREAL_CURSE) is True
    assert misc_screens._is_ethereal(PLAIN_CURSE) is False


def test_discards_the_plain_curse_and_holds_the_ethereal_one():
    # Holding the Ethereal curse exhausts it away for the combat; discarding
    # would only recycle it back into the deck.
    gs = _discard_state([ETHEREAL_CURSE, PLAIN_CURSE])
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Regret"


def test_ethereal_curse_is_not_the_first_pick_over_an_ordinary_card():
    gs = _discard_state([ETHEREAL_CURSE, SLIMED])
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Slimed"


def test_exhaust_effect_is_not_wasted_on_an_ethereal_card():
    # It would exhaust itself anyway -- spend the effect on the other curse.
    gs = _discard_state([ETHEREAL_CURSE, PLAIN_CURSE], prompt="Choose a card to Exhaust.")
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] == "Regret"


def test_exhaust_prompt_does_not_favour_sly():
    # Exhaust removes the card outright -- Sly never triggers, so this is just
    # "lose the worst card".
    gs = _discard_state([SLY_ATTACK, SLIMED], prompt="Choose a card to Exhaust.")
    _, fields = misc_screens.decide_hand_select(gs)
    assert _chosen(gs, fields)["name"] != "Flick-Flack"
