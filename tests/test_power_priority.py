import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture

AFTERIMAGE = {
    "name": "Afterimage", "cost": "1", "type": "Power", "target_type": "Self",
    "description": "Whenever you play a card, gain 1 Block.",
}
ACCURACY = {
    "name": "Accuracy", "cost": "1", "type": "Power", "target_type": "Self",
    "description": "Shivs deal 4 additional damage.",
}
NOXIOUS_FUMES = {
    "name": "Noxious Fumes", "cost": "1", "type": "Power", "target_type": "Self",
    "description": "At the start of your turn, apply 2 Poison to ALL enemies.",
}
WRAITH_FORM = {
    "name": "Wraith Form", "cost": "3", "type": "Power", "target_type": "Self",
    "description": "Gain 2 Intangible. At the start of your turn, lose 1 Dexterity.",
}
BACKSTAB = {"name": "Backstab", "cost": "0", "type": "Attack", "description": "Innate. Deal 11 damage. Exhaust."}
STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "target_type": "Self", "description": "Gain 5 Block."}
MALAISE = {"name": "Malaise", "cost": "1", "type": "Skill", "description": "Enemy loses X Strength. Apply X Weak."}


def _state(cards, enemy_hp=999, attack=None, hp=70, energy=3):
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
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    # No potions: a lethal-threat fixture would otherwise trigger the
    # last-ditch potion use and mask the card decision under test.
    raw["player"]["potions"] = []
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["block"] = 0
    enemy["status"] = []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(attack), "title": "", "description": ""}] if attack else []
    )
    return GameState(raw)


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


def test_power_is_played_before_attacks():
    # Live sample: 50 of 73 turns holding a playable Power played something
    # else first -- Backstab/Ricochet ahead of Accuracy, the very card that
    # makes the Shivs they generate hit harder.
    gs = _state([BACKSTAB, ACCURACY])
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Accuracy"


def test_afterimage_is_played_before_other_cards():
    # Afterimage gives Block per card played, so every card after it benefits.
    gs = _state([STRIKE, DEFEND, AFTERIMAGE], attack=6, hp=30)
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Afterimage"


def test_power_beats_a_plain_skill():
    gs = _state([MALAISE, NOXIOUS_FUMES])
    _, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Noxious Fumes"


def test_lethal_still_outranks_playing_a_power():
    # Winning the exchange now beats scaling for a fight that's ending.
    gs = _state([STRIKE, AFTERIMAGE], enemy_hp=5)
    _, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Strike"


def test_power_skipped_when_it_would_starve_the_block_we_need():
    # 1 energy, a hit that would kill us, and only enough for one card.
    gs = _state([DEFEND, AFTERIMAGE], attack=9, hp=6, energy=1)
    _, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Defend"


def test_wraith_form_counts_as_a_power_despite_losing_dexterity():
    # It loses Dexterity, so the narrow Strength/Dexterity matcher rejects it,
    # but it is still a Power and still wants to be played early.
    assert combat._is_power(WRAITH_FORM) is True
    assert combat._is_scaling_buff(WRAITH_FORM) is True


def test_enemy_debuff_skill_is_not_mistaken_for_a_self_buff():
    assert combat._is_power(MALAISE) is False
    assert combat._is_scaling_buff(MALAISE) is False
