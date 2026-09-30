"""Regressions for the combat-correctness issues found in the second 10-run
evaluation (user notes 1-9, plus the multi-hit intent bug found in the logs)."""
import copy

from bot.game_state import GameState
from bot.strategy import combat, potions as potion_db
from conftest import load_fixture

STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "target_type": "Self", "description": "Gain 5 Block."}
BOUNCING_FLASK = {
    "name": "Bouncing Flask", "cost": "2", "type": "Skill",
    "description": "Apply 3 Poison to a random enemy 3 times.",
}
KNIFE_TRAP_EMPTY = {
    "name": "Knife Trap", "cost": "2", "type": "Skill",
    "description": "Play every Shiv in your Exhaust Pile on the enemy. (Plays 0 Shivs)",
}
KNIFE_TRAP_LOADED = {
    "name": "Knife Trap", "cost": "2", "type": "Skill",
    "description": "Play every Shiv in your Exhaust Pile on the enemy. (Plays 4 Shivs)",
}
CALCULATED_GAMBLE = {
    "name": "Calculated Gamble", "cost": "0", "type": "Skill", "target_type": "Self",
    "description": "Discard your Hand, then draw that many cards. Exhaust.",
}
CHOKE = {
    "name": "Choke", "cost": "2", "type": "Attack",
    "description": "Deal 8 damage. Whenever you play a card this turn, the enemy loses 3 HP.",
}
INFECTION = {
    "name": "Infection", "cost": "0", "type": "Status", "can_play": False,
    "description": "Unplayable. At the end of your turn, if this is in your Hand, take 3 damage.",
}


def _enemy(entity_id="E_0", hp=99, label=None, poison=0, block=0):
    status = [{"id": "POISON_POWER", "name": "Poison", "amount": poison}] if poison else []
    return {
        "entity_id": entity_id, "name": entity_id, "hp": hp, "max_hp": 99, "block": block,
        "status": status,
        "intents": [{"type": "Attack", "label": label, "title": "", "description": ""}] if label else [],
    }


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


# --- multi-hit intents (found in the logs, not reported) -------------------

def test_multi_hit_intent_labels_are_multiplied():
    # 602 of 3849 logged attack intents were "NxM"; reading the first number
    # only under-counted incoming damage by up to 4x.
    assert combat._intent_damage("4x4") == 16
    assert combat._intent_damage("2x3") == 6
    assert combat._intent_damage("8") == 8
    assert combat._intent_damage("") == 0


def test_incoming_damage_counts_all_hits():
    enemies = [_enemy(label="4x4")]
    assert combat._incoming_damage(enemies) == 16
    assert combat._enemy_attack_damage(enemies[0]) == 16


def test_weak_is_not_double_counted():
    # Verified from logs: the game already reduces the label for a Weakened
    # enemy (Twig Slime 4 -> 3), so we must not scale it again.
    weak_enemy = _enemy(label="3")
    weak_enemy["status"] = [{"id": "WEAK_POWER", "name": "Weak", "amount": 1}]
    assert combat._incoming_damage([weak_enemy]) == 3


# --- poison ----------------------------------------------------------------

def test_poison_counts_repeat_multiplier():
    # "Apply 3 Poison ... 3 times" is 9, not 3.
    assert combat._card_poison(BOUNCING_FLASK) == 9


def test_bouncing_flask_outranks_a_strike():
    gs = _state([STRIKE, BOUNCING_FLASK], [_enemy(hp=99)])
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Bouncing Flask"


def test_doomed_enemy_does_not_contribute_incoming_damage():
    # It dies to poison before it acts, so blocking against it is wasted.
    doomed = _enemy(hp=4, poison=9, label="20")
    gs = _state([STRIKE, DEFEND], [doomed])
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) == 0  # no need to block


def test_status_card_damage_still_counted_when_every_enemy_is_doomed():
    # Infection hits us at end of turn regardless of whether anything survives
    # to attack -- this is the case that gets missed if the two are conflated.
    doomed = _enemy(hp=4, poison=9, label="20")
    gs = _state([DEFEND, dict(INFECTION)], [doomed], hp=4)
    assert combat._hand_curse_damage(gs.hand) == 3
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) > 0  # must still block the Infection


DAGGER_SPRAY = {
    "name": "Dagger Spray", "cost": "1", "type": "Attack",
    "description": "Deal 4 damage to ALL enemies twice.",
}


def test_takes_chip_damage_to_deal_more_back():
    # "Take 1 to deal 8" -- the trade the user asked for.
    gs = _state([DEFEND, DAGGER_SPRAY], [_enemy(label="1")], energy=1)
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Dagger Spray"


def test_blocks_a_multi_hit_that_only_looks_like_chip():
    # "2x3" is 6 damage, not 2. Before the multi-hit fix this was mistaken for
    # chip damage and went unblocked.
    gs = _state([DEFEND, DAGGER_SPRAY], [_enemy(label="2x3")], energy=1)
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert combat._card_block(played) > 0


# --- dud cards -------------------------------------------------------------

def test_knife_trap_not_played_with_no_shivs_banked():
    gs = _state([KNIFE_TRAP_EMPTY, STRIKE], [_enemy()])
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Strike"


def test_knife_trap_is_played_when_shivs_are_banked():
    assert combat._is_dud_this_turn(KNIFE_TRAP_LOADED, _state([KNIFE_TRAP_LOADED], [_enemy()])) is False


def test_calculated_gamble_not_burned_on_a_hand_worth_keeping():
    gs = _state([CALCULATED_GAMBLE, STRIKE, DEFEND], [_enemy(label="6")])
    assert combat._is_dud_this_turn(gs.hand[0], gs) is True


def test_calculated_gamble_is_fine_with_a_dead_hand():
    dead = dict(INFECTION)
    gs = _state([CALCULATED_GAMBLE, dead, dict(INFECTION)], [_enemy()])
    assert combat._is_dud_this_turn(gs.hand[0], gs) is False


REFLEX = {"name": "Reflex", "cost": "3", "type": "Skill", "target_type": "Self",
          "description": "Sly. Draw 2 cards."}
BACKFLIP = {"name": "Backflip", "cost": "1", "type": "Skill", "target_type": "Self",
            "description": "Gain 5 Block. Draw 2 cards."}
ADRENALINE = {"name": "Adrenaline", "cost": "0", "type": "Skill", "target_type": "Self",
              "description": "Gain [silent_energy_icon.png]. Draw 2 cards. Exhaust."}


def _full_hand_state(extra):
    filler = [dict(STRIKE) for _ in range(combat.HAND_LIMIT - 1)]
    return _state(filler + [extra], [_enemy()])


def test_pure_draw_card_is_not_played_at_the_hand_cap():
    # Hand caps at 10 (confirmed from logged hand sizes) -- drawing past it
    # throws the drawn cards away.
    gs = _full_hand_state(dict(REFLEX))
    assert len(gs.hand) == combat.HAND_LIMIT
    assert combat._is_dud_this_turn(gs.hand[-1], gs) is True


def test_draw_card_with_other_value_is_still_played_at_the_cap():
    # Backflip still gives Block, Adrenaline still gives Energy.
    gs = _full_hand_state(dict(BACKFLIP))
    assert combat._is_dud_this_turn(gs.hand[-1], gs) is False
    gs = _full_hand_state(dict(ADRENALINE))
    assert combat._is_dud_this_turn(gs.hand[-1], gs) is False


def test_pure_draw_card_is_fine_below_the_cap():
    gs = _state([dict(REFLEX), dict(STRIKE)], [_enemy()])
    assert combat._is_dud_this_turn(gs.hand[0], gs) is False


def test_draw_potion_not_used_at_the_hand_cap():
    swift = {"slot": 0, "name": "Swift Potion", "description": "Draw 3 cards."}
    gs = _full_hand_state(dict(STRIKE))
    assert potion_db._draw_would_be_wasted(swift, gs) is True
    fire = {"slot": 0, "name": "Fire Potion", "description": "Deal 20 damage."}
    assert potion_db._draw_would_be_wasted(fire, gs) is False


# --- ordering --------------------------------------------------------------

def test_choke_is_sequenced_early():
    assert combat._play_timing(CHOKE) == combat.PLAY_EARLY
    assert combat._scales_with_later_plays(CHOKE) is True
    assert combat._scales_with_later_plays(STRIKE) is False


# --- potions ---------------------------------------------------------------

def test_plating_potion_counts_as_defensive():
    heart_of_iron = {"slot": 0, "name": "Heart of Iron", "description": "Gain 7 Plating."}
    assert potion_db._block_value(heart_of_iron) == 7
    assert potion_db.potion_value(heart_of_iron) >= 35


def test_potion_module_counts_multi_hit_intents():
    assert potion_db._incoming_damage([_enemy(label="4x4")]) == 16
