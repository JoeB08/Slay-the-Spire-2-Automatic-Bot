"""Fixes from the 2026-09-11 set, each pinned to the live state that exposed it."""
import copy

from bot.game_state import GameState
from bot.strategy import combat
from conftest import load_fixture


def _live(name):
    return GameState(load_fixture(name)["raw"])


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


# --- a kill must not leave a lethal hit coming -------------------------------

def test_no_kill_that_leaves_a_lethal_hit_coming():
    # Run 15, floor 22: 6 HP, 24 Block, 33 incoming. Strike finished a Tough
    # Egg whose intent was Summon; 9 still came through. Defend was in hand.
    gs = _live("live_egg_kill_while_dying.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


def test_the_same_kill_is_still_taken_when_it_is_safe():
    raw = copy.deepcopy(load_fixture("live_egg_kill_while_dying.json")["raw"])
    raw["player"]["hp"] = 50
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Strike"
    assert fields["target"] == "TOUGH_EGG_2"


# --- Waterfall Giant: potions wait for the death blow ------------------------

def test_giant_potions_wait_for_the_death_blow():
    # Run 3 drank its Energy Potion and run 5 its Skill Potion on round 1,
    # while the Giant only buffed; both death blows later came up short.
    for name in ("live_giant_energy_potion_round1.json", "live_giant_skill_potion_round1.json"):
        action, _ = combat.decide(_live(name))
        assert action != "use_potion", name


# --- Skittish: the Block a Gardener raises when first hit --------------------

def test_skittish_block_is_counted_before_a_two_strike_kill():
    # Run 2, floor 14: Gardener on 7, two Strikes (12) planned -- but the
    # first raises 6 Block, so 13 is needed. 35 incoming at 32 HP.
    gs = _live("live_gardener_first_strike.json")
    action, fields = combat.decide(gs)
    assert not (action == "play_card" and fields.get("target") == "PHANTASMAL_GARDENER_0"
                and _played(gs, fields)["name"] == "Strike")


def test_a_gardener_already_hit_this_turn_keeps_its_hidden_block():
    # After the first Strike the payload read 1 HP and 0 Block; 6 Block was
    # there, and the second Strike was absorbed.
    first = _live("live_gardener_first_strike.json")
    combat._note_hits(first, ("play_card", {"card_index": 2, "target": "PHANTASMAL_GARDENER_0"}))
    gs = _live("live_gardener_hidden_block.json")
    assert combat._enemy_block(gs.enemies[0]) == 6
    action, fields = combat.decide(gs)
    assert not (action == "play_card" and fields.get("target") == "PHANTASMAL_GARDENER_0"
                and _played(gs, fields)["name"] == "Strike")


def test_the_first_hit_on_a_gardener_still_kills():
    raw = copy.deepcopy(load_fixture("live_gardener_first_strike.json")["raw"])
    raw["battle"]["enemies"][0]["hp"] = 5
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Strike"
    assert fields["target"] == "PHANTASMAL_GARDENER_0"


# --- Smoggy and the Gas Bomb -------------------------------------------------

def test_no_chip_into_a_gas_bomb_that_explodes_anyway():
    # Run 10, floor 7: Strike (6) into a 7-HP Gas Bomb, which blew up on its
    # own turn regardless, while Living Fog took nothing.
    gs = _live("live_smoggy_bomb_chip.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields.get("target") != "GAS_BOMB_0"


IRON_WALL = {"id": "IRON_WALL", "name": "Iron Wall", "type": "Skill", "cost": "2",
             "description": "Gain 9 Block.", "target_type": "Self", "can_play": True, "keywords": []}
DEFEND = {"id": "DEFEND_SILENT", "name": "Defend", "type": "Skill", "cost": "1",
          "description": "Gain 5 Block.", "target_type": "Self", "can_play": True, "keywords": []}


def test_one_skill_a_turn_plays_the_biggest_block():
    # Smoggy: "You can only play 1 Skill per turn." Two Defends (10) beat one
    # 9-Block card only if both can be played; under Smoggy one Defend is 5.
    raw = copy.deepcopy(load_fixture("live_smoggy_one_skill.json")["raw"])
    raw["player"].update(energy=2, block=0,
                         hand=[dict(c, index=i) for i, c in enumerate([IRON_WALL, DEFEND, DEFEND])])
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Iron Wall"


def test_without_smoggy_two_defends_still_beat_one_card():
    raw = copy.deepcopy(load_fixture("live_smoggy_one_skill.json")["raw"])
    raw["player"].update(energy=2, block=0, status=[],
                         hand=[dict(c, index=i) for i, c in enumerate([IRON_WALL, DEFEND, DEFEND])])
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Defend"


# --- Sly cards that only draw or give energy are not paid for ----------------

def test_reflex_is_not_paid_for_with_attacks_in_hand():
    # Run 3, Giant round 4: 3 energy on Reflex ("Sly. Draw 2 cards.") with
    # Pounce + Strike (20) in hand, on a turn the Giant only healed.
    gs = _live("live_reflex_paid.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] != "Reflex"


# --- draw first when the Block in hand falls short ---------------------------

def test_draws_before_blocking_when_the_block_falls_short():
    # Run 3, the Giant's death blow: 45 into 30 HP. Both Defends (14) went
    # first and Acrobatics drew with no energy left to play what it found.
    gs = _live("live_block_before_draw.json")
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Acrobatics"


# --- Lagavulin Matriarch: attack on the turn it wakes anyway -----------------

def _asleep(raw, turns):
    for e in raw["battle"]["enemies"]:
        for s in e.get("status") or []:
            if s.get("name") == "Asleep":
                s["amount"] = turns
    return GameState(raw)


def test_attacks_are_playable_on_the_matriarchs_last_asleep_turn():
    # Set 4, run 3 (the user's call): Asleep 1, 11 Block, nothing coming. The
    # bot played Survivor and two Defends with Strike+ and Strike in hand --
    # every recorded Matriarch fight spent that turn on Block.
    gs = _live("live_lagavulin_last_asleep_turn.json")
    playable = [c["name"] for c in combat._playable_hand(gs)]
    assert "Strike+" in playable and "Strike" in playable


def test_attacks_still_wait_while_the_matriarch_has_turns_left_asleep():
    raw = copy.deepcopy(load_fixture("live_lagavulin_last_asleep_turn.json")["raw"])
    gs = _asleep(raw, 2)
    playable = [c["name"] for c in combat._playable_hand(gs)]
    assert "Strike" not in playable


# --- card rewards take a key payoff first ------------------------------------

SHIV_DECK = ["Strike"] * 4 + ["Defend"] * 4 + ["Blade Dance", "Leading Strike", "Cloak and Dagger"]


def test_card_reward_takes_accuracy_in_a_shiv_deck():
    from bot.strategy import cards as card_db
    offer = [{"index": 0, "name": "Prepared"}, {"index": 1, "name": "Accuracy"}]
    assert card_db.best_card_reward_index(offer, SHIV_DECK, act=1, floor=6) == 1


def test_card_reward_does_not_force_accuracy_without_shiv_makers():
    from bot.strategy import cards as card_db
    offer = [{"index": 0, "name": "Prepared"}, {"index": 1, "name": "Accuracy"}]
    deck = ["Strike"] * 5 + ["Defend"] * 5 + ["Deadly Poison"]
    assert card_db.best_card_reward_index(offer, deck, act=1, floor=6) != 1
