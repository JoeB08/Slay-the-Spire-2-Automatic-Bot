"""Fixes from the 2026-09-10 set, each pinned to the live state that exposed it.

The three `live_*` fixtures are the exact payloads the bot decided on when it
went wrong, so these tests prove the fix against the real input rather than a
hand-built approximation of it.
"""
import copy

from bot.game_state import GameState
from bot.strategy import boss_intel, combat, misc_screens
from conftest import load_fixture


def _live(name):
    return GameState(load_fixture(name)["raw"])


def _played(gs, fields):
    return next(c for c in gs.hand if c["index"] == fields["card_index"])


DEFEND = {"id": "DEFEND_SILENT", "name": "Defend", "type": "Skill", "cost": "1",
          "description": "Gain 5 Block.", "target_type": "Self", "can_play": True, "keywords": []}
UNTOUCHABLE = {"id": "UNTOUCHABLE", "name": "Untouchable", "type": "Skill", "cost": "2",
               "description": "Sly. Gain 6 Block.", "target_type": "Self", "can_play": True,
               "keywords": []}
UNTOUCHABLE_PLUS = dict(UNTOUCHABLE, name="Untouchable+", description="Sly. Gain 9 Block.")
NORMALITY = {"id": "NORMALITY", "name": "Normality", "type": "Curse", "cost": "0",
             "description": "Unplayable. You cannot play more than 3 cards this turn. (1 card left)",
             "target_type": "None", "can_play": False, "keywords": []}


def _turn(hand, energy, attack, hp=53):
    """A real combat payload with the hand and the single attacker swapped in."""
    raw = copy.deepcopy(load_fixture("live_illusion_minion.json")["raw"])
    raw["player"].update(hp=hp, max_hp=70, energy=energy, block=0, status=[], potions=[],
                         hand=[dict(c, index=i) for i, c in enumerate(hand)])
    raw["battle"]["enemies"] = [{
        "entity_id": "FOGMOG_0", "name": "Fogmog", "hp": 43, "max_hp": 74, "block": 0,
        "status": [],
        "intents": [{"type": "Attack", "label": str(attack),
                     "description": f"This enemy intends to Attack for {attack} damage."}],
    }]
    return GameState(raw)


# --- incoming damage has one source of truth ---------------------------------

def test_death_blow_counts_as_incoming():
    # "It will attack you for 51 damage before being destroyed" is typed
    # DeathBlow, not Attack -- and read as 0 by the survival math.
    gs = _live("live_giant_death_blow.json")
    assert combat._incoming_damage(gs.enemies) == 51


def test_dying_turn_drinks_the_potion_instead_of_hitting_the_husk():
    # 13 HP against 51 with a Colorless Potion in the belt: the bot attacked a
    # 999,999,983 HP husk and died holding it.
    gs = _live("live_giant_death_blow.json")
    assert combat.decide(gs) == ("use_potion", {"slot": 0})


def test_death_blow_turn_is_spent_on_block_not_damage():
    # The Giant is a 999,999,983 HP husk; the fight ends after its hit. With
    # the potion gone, the turn should go to Defends, and no pure-damage card
    # should even be considered.
    raw = copy.deepcopy(load_fixture("live_giant_death_blow.json")["raw"])
    raw["player"]["potions"] = []
    gs = GameState(raw)
    playable = [c["name"] for c in combat._playable_hand(gs)]
    for pure_damage in ("Strike", "Thrumming Hatchet", "Leading Strike+", "Haze"):
        assert pure_damage not in playable
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


DEATH_BLOW = [{"type": "DeathBlow", "label": "8",
               "description": "It will attack you for 8 damage before being destroyed."}]


def test_gas_bomb_is_still_a_target_it_has_real_hp():
    # Same DeathBlow intent, but 7/7 HP: killing it may stop the blast.
    bomb = {"entity_id": "GAS_BOMB_0", "name": "Gas Bomb", "hp": 7, "max_hp": 7, "block": 0,
            "status": [], "intents": DEATH_BLOW}
    assert not combat._is_spent(bomb)


def test_only_the_husk_is_spent():
    husk = {"entity_id": "WATERFALL_GIANT_0", "name": "Waterfall Giant", "hp": 999999983,
            "max_hp": 999999999, "block": 0, "status": [], "intents": DEATH_BLOW}
    assert combat._is_spent(husk)


def test_back_attack_reaches_the_survival_math():
    crusher = {
        "entity_id": "CRUSHER_0", "name": "Crusher", "hp": 188, "max_hp": 188, "block": 0,
        "status": [{"name": "Back Attack", "amount": 1,
                    "description": "Deals 50% more damage when it is attacking you from behind."}],
        "intents": [{"type": "Attack", "label": "10",
                     "description": "This enemy intends to Attack for 10 damage."}],
    }
    assert combat._incoming_damage([crusher]) == 15


# --- The Gambit ---------------------------------------------------------------

def test_gambit_makes_one_point_of_chip_worth_blocking():
    # 13 Block against 7x2 with Defend and Strike left: the bot played the
    # Strike, took 1, and died at 69/70 HP.
    gs = _live("live_gambit_chip.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


def test_never_volunteers_for_the_gambit():
    offered = [
        {"index": 0, "name": "The Gambit",
         "description": "Gain 50 Block. If you take unblocked attack damage this combat, die."},
        {"index": 1, "name": "Stratagem",
         "description": "Whenever you shuffle your Draw Pile, choose a card from it to put "
                        "into your Hand."},
    ]
    deck = ["Strike"] * 5 + ["Defend"] * 5
    assert misc_screens._pick_best(offered, deck)["name"] == "Stratagem"


# --- leaders first, minions not ignored ---------------------------------------

def test_minions_are_still_killed_while_the_leader_is_out_of_reach():
    # Killing Eye with Teeth on our turn stops its 3 Status cards (216 logged
    # turns: 0.31 cards added, against 2.5 when left alive), and Fogmog at 37
    # is not within two turns of this hand -- so the kill stands.
    gs = _live("live_illusion_minion.json")
    ctx = combat._turn_context(gs, gs.enemies, combat._playable_hand(gs))
    assert ctx.leader["entity_id"] == "FOGMOG_0"
    assert ctx.focus_leader is None
    assert "EYE_WITH_TEETH_0" in [e["entity_id"] for e in ctx.active_enemies]


def test_a_far_off_leader_does_not_draw_the_chip_damage():
    # Set 2, run 4: Kin Priest at 190 with two attacking Kin Followers. The
    # first merged version aimed non-lethal Strikes at the Priest, which was
    # nowhere near dying; nothing died in eight rounds. Out of reach and not
    # re-summoning, the leader gets no special claim on chip damage.
    gs = _live("live_kin_priest_far_leader.json")
    ctx = combat._turn_context(gs, gs.enemies, combat._playable_hand(gs))
    assert ctx.leader is not None and ctx.leader["entity_id"] == "KIN_PRIEST_0"
    assert ctx.focus_leader is None
    action, fields = combat.decide(gs)
    if action == "play_card" and fields.get("target"):
        assert fields["target"] != "KIN_PRIEST_0"


def test_a_re_summoning_leader_still_draws_the_chip_damage():
    ovicopter = {"entity_id": "OVICOPTER_0", "name": "Ovicopter", "hp": 110, "max_hp": 125,
                 "block": 0, "status": [],
                 "intents": [{"type": "Summon", "label": "",
                              "description": "This enemy intends to summon Monsters."}]}
    assert combat._resummons(ovicopter)
    kin_priest = {"entity_id": "KIN_PRIEST_0", "name": "Kin Priest", "hp": 190, "max_hp": 190,
                  "block": 0, "status": [],
                  "intents": [{"type": "Attack", "label": "8",
                               "description": "This enemy intends to Attack for 8 damage."}]}
    assert not combat._resummons(kin_priest)


def test_chip_goes_to_the_leader_when_no_minion_is_attacking():
    # Ovicopter out of reach and attacking; its minions are eggs, which only
    # summon. Chip into an egg that is about to hatch buys nothing.
    raw = copy.deepcopy(load_fixture("live_ovicopter_minions.json")["raw"])
    for enemy in raw["battle"]["enemies"]:
        if enemy["name"] == "Ovicopter":
            enemy["hp"] = 120
            enemy["intents"] = [{"type": "Attack", "label": "7",
                                 "description": "This enemy intends to Attack for 7 damage."}]
        else:
            enemy["hp"] = 40
            enemy["intents"] = [{"type": "Summon", "label": "",
                                 "description": "This enemy intends to summon Monsters."}]
    strikes = [c for c in raw["player"]["hand"] if c["name"] == "Strike"][:1]
    raw["player"]["hand"] = [dict(c, index=i) for i, c in enumerate(strikes)]
    raw["player"]["energy"] = 1
    raw["player"]["block"] = 30
    gs = GameState(raw)
    ctx = combat._turn_context(gs, gs.enemies, combat._playable_hand(gs))
    assert ctx.focus_leader is None
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields.get("target") == "OVICOPTER_0"


def test_a_leader_in_reach_takes_the_damage_ahead_of_its_minions():
    # Run 10: Ovicopter at 51 with two 20-damage Strikes in hand, and the bot
    # spent them on Tough Eggs -- it re-summoned eggs for ten rounds and won.
    gs = _live("live_ovicopter_minions.json")
    ctx = combat._turn_context(gs, gs.enemies, combat._playable_hand(gs))
    assert ctx.focus_leader is not None and ctx.focus_leader["entity_id"] == "OVICOPTER_0"

    # The turn opens on a Defend; what matters is where the two Strikes go
    # after it. Same payload, the Defends spent: 2 energy, two Strikes.
    raw = copy.deepcopy(load_fixture("live_ovicopter_minions.json")["raw"])
    strikes = [c for c in raw["player"]["hand"] if c["name"] == "Strike"]
    raw["player"]["hand"] = [dict(c, index=i) for i, c in enumerate(strikes)]
    raw["player"]["energy"] = 2
    raw["player"]["block"] = 30
    gs = GameState(raw)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert fields.get("target") == "OVICOPTER_0"


def test_minion_flag_names_the_leader():
    gs = _live("live_illusion_minion.json")
    by_id = {e["entity_id"]: e for e in gs.enemies}
    assert combat._is_leader(by_id["FOGMOG_0"], gs.enemies)
    assert not combat._is_leader(by_id["EYE_WITH_TEETH_0"], gs.enemies)


# --- the most Block for the energy ------------------------------------------

def test_two_defends_beat_one_untouchable():
    # Live: 15 incoming, 2 energy, three Defends and Untouchable. The biggest
    # single card (6 Block for 2 energy) was played over 10 from two Defends.
    gs = _turn([DEFEND, DEFEND, UNTOUCHABLE, DEFEND], energy=2, attack=15)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Defend"


def test_block_plan_covers_the_most_for_the_energy():
    plan = combat._best_block_plan([DEFEND, DEFEND, UNTOUCHABLE, DEFEND], energy=3,
                                   dexterity=0, need=15)
    assert sorted(c["name"] for c in plan) == ["Defend", "Defend", "Defend"]


def test_normality_caps_the_plan_by_plays_not_just_energy():
    gs = _turn([DEFEND, DEFEND, UNTOUCHABLE_PLUS, NORMALITY], energy=3, attack=24)
    assert combat._plays_left(gs) == 1
    plan = combat._best_block_plan([DEFEND, DEFEND, UNTOUCHABLE_PLUS], energy=3,
                                   dexterity=0, need=24, max_cards=1)
    assert [c["name"] for c in plan] == ["Untouchable+"]


# --- Sly cards are kept for a free discard -----------------------------------

def test_sly_block_card_is_kept_when_the_rest_leave_only_chip():
    # Live, floor 30: 12 unblocked at 30 HP with Untouchable, Defend and two
    # Strikes. Paying 2 energy for Untouchable left nothing for the Strikes.
    gs = _live("live_untouchable_paid_for.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] != "Untouchable"


def test_sly_card_still_blocks_when_nothing_else_can():
    gs = _turn([UNTOUCHABLE_PLUS, DEFEND], energy=3, attack=20)
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Untouchable+"


# --- random-target damage is not a promised kill -----------------------------

RICOCHET = {"id": "RICOCHET", "name": "Ricochet", "type": "Attack", "cost": "2",
            "description": "Sly. Deal 3 damage to a random enemy 4 times.",
            "target_type": "AnyEnemy", "can_play": True, "keywords": []}


def test_random_target_damage_only_counts_with_one_enemy_left():
    one = [{"entity_id": "A", "hp": 10}]
    two = [{"entity_id": "A", "hp": 10}, {"entity_id": "B", "hp": 30}]
    assert combat._certain_on(RICOCHET, one)
    assert not combat._certain_on(RICOCHET, two)
    assert combat._certain_on(DEFEND, two)


def test_a_ricochet_kill_is_not_promised_against_two_enemies():
    # Run 20, round 5: 31 incoming at 56 HP. Strike + Ricochet was counted as
    # 18 into a 13 HP Cultist, so the bot skipped blocking; 3 landed, the
    # Cultist lived, and the bot took all 31.
    gs = _live("live_ricochet_random_kill.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert combat._effective_block(_played(gs, fields), combat._player_dexterity(gs)) > 0


# --- a hand-discard whose discard is the payoff -------------------------------

def test_storm_of_steel_is_not_held_back_when_its_discard_wins():
    # Run 13's last turn: Entomancer at 8, the bot on 3 HP, Haze (Sly) and
    # Storm of Steel in hand. Storm was written off as a dud because Haze was
    # affordable; the bot paid 3 energy for Haze and died 8 HP short.
    gs = _live("live_storm_of_steel_lethal.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"] == "Storm of Steel"


def test_a_plain_hand_discard_still_waits_for_the_energy_to_go():
    storm = {"id": "STORM_OF_STEEL", "name": "Storm of Steel", "type": "Skill", "cost": "1",
             "description": "Discard your Hand. Add 1 Shiv into your Hand for each card discarded.",
             "target_type": "Self", "can_play": True, "keywords": []}
    # No Sly card, and one Shiv into a 43 HP Fogmog kills nothing.
    gs = _turn([storm, DEFEND], energy=3, attack=8)
    assert combat._is_dud_this_turn(gs.hand[0], gs)


# --- Echoing Slash's chain --------------------------------------------------

def test_echoing_slash_chain_is_simulated_wave_by_wave():
    card = {"name": "Echoing Slash+", "description": "Deal 18 damage to ALL enemies. "
            "Repeat this effect for each enemy killed.", "cost": "1"}
    eggs = [{"entity_id": f"EGG_{i}", "hp": hp, "block": 0, "status": [], "intents": []}
            for i, hp in enumerate((18, 17, 16))]
    ovicopter = {"entity_id": "OVI", "hp": 73, "block": 0, "status": [], "intents": []}
    kills, clears = combat._chain_kills(card, eggs + [ovicopter], 0)
    assert kills == 3 and not clears  # four waves of 18: Ovicopter left on 1


def test_echoing_slash_goes_first_when_its_chain_kills():
    # Live, floor 23: the chain would have killed three eggs and put 72 into
    # Ovicopter; the bot played Murder+ instead.
    gs = _live("live_echoing_slash_chain.json")
    action, fields = combat.decide(gs)
    assert action == "play_card"
    assert _played(gs, fields)["name"].startswith("Echoing Slash")


# --- Tainted ------------------------------------------------------------------

def test_tainted_skills_that_block_nothing_wait_while_an_attack_comes():
    # Infested Prism, Vital Spark: every Skill adds Tainted, +N per attack hit.
    gs = _live("live_tainted_skills.json")
    playable = [c["name"] for c in combat._playable_hand(gs)]
    assert "Acrobatics+" not in playable  # draws, blocks nothing, adds 2 per hit
    assert "Defend+" in playable          # its Block still outweighs the Tainted


def test_tainted_stacks_count_toward_incoming():
    gs = _turn([DEFEND], energy=3, attack=10, hp=60)
    gs.raw["player"]["status"] = [{"name": "Tainted", "amount": 6,
                                   "description": "Take 6 additional damage from Attacks this turn."}]
    gs = GameState(gs.raw)
    ctx = combat._turn_context(gs, gs.enemies, combat._playable_hand(gs))
    assert ctx.incoming == 16


# --- Fan of Knives against a crowd -------------------------------------------

FAN_OF_KNIVES = {"id": "FAN_OF_KNIVES", "name": "Fan of Knives", "type": "Power", "cost": "2",
                 "description": "Shivs now hit ALL enemies. Add 4 Shivs into your Hand.",
                 "target_type": "Self", "can_play": True, "keywords": []}


def test_fan_of_knives_goes_first_against_several_enemies():
    # Floor 9, three enemies: Pounce killed a 9 HP slime that four all-enemy
    # Shivs would have killed anyway, with 16 into each of the other two.
    gs = _live("live_fan_of_knives_three_enemies.json")
    action, fields = combat.decide(gs)
    assert _played(gs, fields)["name"] == "Fan of Knives"


def test_fan_of_knives_has_no_special_priority_against_one_enemy():
    gs = _turn([FAN_OF_KNIVES, DEFEND], energy=3, attack=8)
    ctx = combat._turn_context(gs, gs.enemies, combat._playable_hand(gs))
    assert combat._step_fan_of_knives(gs, combat._playable_hand(gs), ctx) is None


# --- potions that hand us cards, and Entropic Brew ---------------------------

def _potion(slot, name, description):
    return {"id": name.upper().replace(" ", "_"), "name": name, "description": description,
            "slot": slot, "can_use_in_combat": True, "target_type": "Self", "keywords": []}


POWER_POTION = ("Power Potion", "Choose 1 of 3 random Power cards to add into your Hand. "
                "It's free to play this turn.")
ENTROPIC_BREW = ("Entropic Brew", "Fill all your empty potion slots with random potions.")
BLOCK_POTION = ("Block Potion", "Gain 12 Block.")


def test_card_potions_are_spent_in_a_boss_fight():
    # A live run carried a Power Potion through all 19 rounds of the act 1 boss.
    gs = _turn([DEFEND], energy=3, attack=10, hp=60)
    gs.raw["state_type"] = "boss"
    gs.raw["player"]["potions"] = [_potion(0, *POWER_POTION)]
    gs = GameState(gs.raw)
    from bot.strategy import potions

    assert potions.suggest_potion_use(gs) == ("use_potion", {"slot": 0})


def test_entropic_brew_is_drunk_with_room_to_fill():
    gs = _turn([DEFEND], energy=3, attack=0, hp=60)
    gs.raw["player"]["potions"] = [_potion(0, *ENTROPIC_BREW)]
    gs.raw["player"]["max_potion_slots"] = 3
    gs = GameState(gs.raw)
    from bot.strategy import potions

    assert potions.suggest_potion_use(gs) == ("use_potion", {"slot": 0})


def test_entropic_brew_waits_while_the_belt_is_full():
    gs = _turn([DEFEND], energy=3, attack=0, hp=60)
    gs.raw["player"]["potions"] = [_potion(0, *ENTROPIC_BREW), _potion(1, *POWER_POTION),
                                   _potion(2, *BLOCK_POTION)]
    gs.raw["player"]["max_potion_slots"] = 3
    gs = GameState(gs.raw)
    from bot.strategy import potions

    assert potions.suggest_potion_use(gs) != ("use_potion", {"slot": 0})


# --- boss intel ----------------------------------------------------------------

def test_boss_intel_reads_the_map_and_forgets_it_on_a_new_act():
    boss_intel.reset()
    boss_intel.note_raw({"run": {"act": 1}, "map": {"boss": {"name": "Lagavulin Matriarch"}}})
    assert boss_intel.current_boss() == "Lagavulin Matriarch"
    assert boss_intel.preferred_archetype() == "poison"
    boss_intel.note_raw({"run": {"act": 2}})
    assert boss_intel.current_boss() is None
    boss_intel.reset()
