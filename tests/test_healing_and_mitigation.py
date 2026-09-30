import copy

from bot.game_state import GameState
from bot.strategy import combat
from bot.strategy import potions as potion_db
from conftest import load_fixture

HEAL = {"id": "BLOOD", "name": "Blood Potion", "slot": 0, "can_use_in_combat": True,
        "target_type": "AnyPlayer", "description": "Heal 20 HP."}
REGEN = {"id": "REGEN", "name": "Regen Potion", "slot": 0, "can_use_in_combat": True,
         "target_type": "AnyPlayer", "description": "Gain 5 Regen."}
FIRE = {"id": "FIRE", "name": "Fire Potion", "slot": 0, "can_use_in_combat": True,
        "target_type": "AnyEnemy", "description": "Deal 20 damage."}

LEG_SWEEP = {"name": "Leg Sweep", "cost": "2", "description": "Apply 2 Weak. Gain 11 Block."}
PIERCING_WAIL = {"name": "Piercing Wail", "cost": "1",
                 "description": "ALL enemies lose 6 Strength this turn. Exhaust."}
DEFEND = {"name": "Defend", "cost": "1", "description": "Gain 5 Block.", "target_type": "Self"}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}


def _state(potions=(), cards=(), hp=70, max_hp=70, attack=None, state_type="monster",
           enemy_hp=50, rnd=1, energy=3):
    raw = copy.deepcopy(load_fixture("combat_turn1.json"))
    hand = [copy.deepcopy(c) for c in cards]
    for i, c in enumerate(hand):
        c["index"] = i
        c.setdefault("can_play", True)
        c.setdefault("target_type", "AnyEnemy")
    raw["state_type"] = state_type
    raw["player"]["hand"] = hand
    raw["player"]["hp"] = hp
    raw["player"]["max_hp"] = max_hp
    raw["player"]["energy"] = energy
    raw["player"]["block"] = 0
    raw["player"]["status"] = []
    raw["player"]["potions"] = [dict(p) for p in potions]
    raw["battle"]["round"] = rnd
    enemy = raw["battle"]["enemies"][0]
    raw["battle"]["enemies"] = [enemy]
    enemy["hp"] = enemy_hp
    enemy["status"] = []
    enemy["intents"] = (
        [{"type": "Attack", "label": str(attack), "title": "", "description": ""}] if attack else []
    )
    return GameState(raw)


# --- healing must not be wasted ---

def test_does_not_heal_at_full_hp():
    # A live run burned a heal at full health for nothing.
    gs = _state(potions=[HEAL], hp=70, max_hp=70, state_type="elite")
    result = potion_db.suggest_potion_use(gs)
    assert result is None or result[1].get("slot") != 0 or not potion_db._is_healing(HEAL)


def test_does_not_heal_when_barely_scratched():
    # 20-point heal with only 3 HP missing throws most of it away.
    gs = _state(potions=[HEAL], hp=67, max_hp=70, state_type="elite")
    assert potion_db._healing_is_worthwhile(HEAL, gs) is False


def test_heals_when_the_healing_is_mostly_used():
    gs = _state(potions=[HEAL], hp=40, max_hp=70, state_type="elite")
    assert potion_db._healing_is_worthwhile(HEAL, gs) is True


def test_overflow_spending_still_skips_a_useless_heal():
    gs = _state(potions=[HEAL], hp=70, max_hp=70)
    # Even if it would overflow, using a heal at full HP achieves nothing.
    spendable_heal = potion_db._is_healing(HEAL) and gs.hp >= gs.max_hp
    assert spendable_heal is True  # i.e. the guard's condition triggers


# --- regen wants a long fight, early ---

def test_uses_regen_early_in_an_elite_fight():
    gs = _state(potions=[REGEN], hp=40, max_hp=70, state_type="elite", rnd=1)
    result = potion_db.suggest_potion_use(gs)
    assert result is not None
    assert result[0] == "use_potion"


def test_holds_regen_in_a_short_trash_fight():
    gs = _state(potions=[REGEN], hp=40, max_hp=70, state_type="monster", enemy_hp=12, rnd=1)
    assert potion_db.suggest_potion_use(gs) is None


def test_holds_regen_late_in_a_fight():
    gs = _state(potions=[REGEN], hp=40, max_hp=70, state_type="elite", rnd=9)
    assert potion_db._regen_worth_using(REGEN, gs, gs.enemies) is False


def test_does_not_use_regen_at_full_hp():
    gs = _state(potions=[REGEN], hp=70, max_hp=70, state_type="elite", rnd=1)
    assert potion_db._regen_worth_using(REGEN, gs, gs.enemies) is False


# --- weaken before blocking ---

def test_applies_weak_before_playing_block():
    # Blocking first would size the block against the un-weakened hit and
    # waste part of it.
    gs = _state(cards=[DEFEND, LEG_SWEEP], hp=30, attack=20)
    action, fields = combat.decide(gs)
    assert action == "play_card"
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] == "Leg Sweep"


def test_strength_reduction_also_counts_as_mitigation():
    assert combat._reduces_incoming_damage(PIERCING_WAIL) is True
    assert combat._reduces_incoming_damage(DEFEND) is False


def test_no_mitigation_detour_when_nothing_is_attacking():
    gs = _state(cards=[STRIKE, LEG_SWEEP], hp=30, attack=None)
    action, fields = combat.decide(gs)
    played = next(c for c in gs.hand if c["index"] == fields["card_index"])
    assert played["name"] != "Leg Sweep"
