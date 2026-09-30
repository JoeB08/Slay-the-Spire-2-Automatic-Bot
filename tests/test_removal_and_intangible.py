import copy

from bot.game_state import GameState
from bot.strategy import cards as card_db
from bot.strategy import combat, misc_screens
from conftest import load_fixture

STRIKE = {"name": "Strike", "type": "Attack", "description": "Deal 6 damage."}
DEFEND = {"name": "Defend", "type": "Skill", "description": "Gain 5 Block."}
SLICE = {"name": "Slice", "type": "Attack", "description": "Deal 6 damage."}
ADRENALINE = {"name": "Adrenaline", "type": "Skill", "description": "Gain 1 Energy. Draw 2 cards. Exhaust."}
CURSE = {"name": "Regret", "type": "Curse", "description": "Unplayable."}


# --- removal priority ---

def test_starter_cards_outrank_weak_real_cards_for_removal():
    # The old inverse-pick-rate ranking removed Slice (pick rate 5) and kept
    # the Strike, which is backwards for thinning a Silent deck.
    assert card_db.removal_priority(STRIKE) > card_db.removal_priority(SLICE)
    assert card_db.removal_priority(DEFEND) > card_db.removal_priority(SLICE)


def test_curses_outrank_everything_for_removal():
    assert card_db.removal_priority(CURSE) > card_db.removal_priority(STRIKE)


def test_good_cards_are_the_last_thing_removed():
    assert card_db.removal_priority(ADRENALINE) < card_db.removal_priority(SLICE)
    assert card_db.removal_priority(ADRENALINE) < card_db.removal_priority(STRIKE)


def test_more_copies_makes_a_starter_a_better_cut():
    many = ["Strike"] * 5
    few = ["Strike"]
    assert card_db.removal_priority(STRIKE, many) > card_db.removal_priority(STRIKE, few)


def test_removal_prompt_picks_a_starter_over_a_weak_real_card():
    raw = {
        "state_type": "card_select",
        "run": {"act": 1, "floor": 5},
        "card_select": {
            "screen_type": "simple_select",
            "prompt": "Choose a card to Remove.",
            "cards": [
                {"index": 0, "id": "SLICE", "name": "Slice", "type": "Attack"},
                {"index": 1, "id": "STRIKE_SILENT", "name": "Strike", "type": "Attack"},
            ],
            "preview_showing": False,
            "can_cancel": False,
            "can_confirm": False,
        },
        "player": {
            "hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3,
            "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        },
    }
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    gs = GameState(raw)
    action, fields = misc_screens.decide_card_select(gs)
    assert action == "select_card"
    offered = {c["index"]: c["name"] for c in raw["card_select"]["cards"]}
    assert offered[fields["index"]] == "Strike"


UPGRADE_RELIC = {
    "id": "SOME_RELIC",
    "name": "Refiner",
    "description": "Whenever you remove a card, add it back Upgraded.",
}


def _removal_screen(cards, relics=()):
    return GameState(
        {
            "state_type": "card_select",
            "run": {"act": 1, "floor": 5},
            "card_select": {
                "screen_type": "simple_select",
                "prompt": "Choose a card to Remove.",
                "cards": [dict(c, index=i) for i, c in enumerate(cards)],
                "preview_showing": False,
                "can_cancel": False,
                "can_confirm": False,
            },
            "player": {
                "hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3,
                "relics": list(relics),
                "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def _pick_name(gs, fields):
    return next(c["name"] for c in gs.raw["card_select"]["cards"] if c["index"] == fields["index"])


def test_upgrade_relic_avoids_starters_on_a_removal_screen():
    # With the relic the card comes back Upgraded, so feeding it a Strike
    # wastes the effect -- the opposite of a normal removal.
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    gs = _removal_screen(
        [dict(STRIKE), dict(SLICE), dict(ADRENALINE), dict(CURSE)], relics=[UPGRADE_RELIC]
    )
    _, fields = misc_screens.decide_card_select(gs)
    assert _pick_name(gs, fields) not in ("Strike", "Regret")


def test_without_the_relic_a_removal_screen_still_targets_starters():
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    gs = _removal_screen([dict(STRIKE), dict(SLICE), dict(ADRENALINE)])
    _, fields = misc_screens.decide_card_select(gs)
    assert _pick_name(gs, fields) == "Strike"


def test_upgrade_target_picks_the_biggest_real_upgrade():
    # Superseded the old "pick the median card" proxy: per-card upgrade values
    # are now imported from the game, so the pick is made on actual benefit.
    # Adrenaline gains a whole extra Energy; Deadly Poison gains 2 Poison.
    weak, mid, best = dict(SLICE), {"name": "Deadly Poison", "type": "Skill"}, dict(ADRENALINE)
    chosen = card_db.pick_upgrade_target([weak, mid, best])
    assert chosen["name"] == "Adrenaline"


def test_upgrade_value_reflects_real_per_card_gains():
    # Cost reductions and extra energy dominate; small stat bumps don't.
    assert card_db.upgrade_value("Adrenaline") > card_db.upgrade_value("Deadly Poison")
    assert card_db.upgrade_value("Tools of the Trade") > card_db.upgrade_value("Noxious Fumes")
    # Junk can't be upgraded usefully at all.
    assert card_db.upgrade_value("Infection") == 0.0


def test_upgrade_target_skips_curses_and_starters():
    chosen = card_db.pick_upgrade_target([dict(CURSE), dict(STRIKE), dict(SLICE)])
    assert chosen["name"] == "Slice"


def test_relic_detection_reads_description_not_a_name_list():
    gs = _removal_screen([dict(SLICE)], relics=[UPGRADE_RELIC])
    assert misc_screens._removal_returns_upgraded(gs) is True
    gs2 = _removal_screen([dict(SLICE)], relics=[{"name": "X", "description": "Gain 1 gold."}])
    assert misc_screens._removal_returns_upgraded(gs2) is False


# --- intangible enemies ---

SHIV_MAKER = {"name": "Blade Dance", "cost": "1", "description": "Add 3 Shivs into your Hand. Exhaust."}
BACKSTAB = {"name": "Backstab", "cost": "0", "description": "Innate. Deal 11 damage. Exhaust."}


def _enemy(intangible=False, hp=50):
    return {
        "entity_id": "E_0", "name": "Foe", "hp": hp, "max_hp": 99, "block": 0,
        "status": ([{"id": "INTANGIBLE_POWER", "name": "Intangible", "amount": 2}] if intangible else []),
        "intents": [],
    }


def test_hit_count_counts_each_shiv_separately():
    assert combat._hit_count(BACKSTAB) == 1
    assert combat._hit_count(SHIV_MAKER) == 3


def test_against_intangible_many_small_hits_beat_one_big_attack():
    enemy = _enemy(intangible=True)
    # Backstab's 11 damage is reduced to 1; three shivs land 3.
    assert combat._effective_damage(SHIV_MAKER, enemy, 0) > combat._effective_damage(BACKSTAB, enemy, 0)


BIG_HIT = {"name": "Skewer", "cost": "1", "description": "Deal 30 damage."}


def test_without_intangible_a_bigger_single_hit_still_wins():
    enemy = _enemy(intangible=False)
    assert combat._effective_damage(BIG_HIT, enemy, 0) > combat._effective_damage(SHIV_MAKER, enemy, 0)


def test_intangible_inverts_the_preference_toward_multi_hit():
    plain, intang = _enemy(False), _enemy(True)
    # A 30-damage single hit dominates normally...
    assert combat._effective_damage(BIG_HIT, plain, 0) > combat._effective_damage(SHIV_MAKER, plain, 0)
    # ...but under Intangible it lands for 1 while three shivs land 3.
    assert combat._effective_damage(BIG_HIT, intang, 0) < combat._effective_damage(SHIV_MAKER, intang, 0)
