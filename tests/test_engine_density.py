"""Recommendation 2: own one per-turn damage engine, and keep it drawable.

The only act 1 boss fight on record that was won held `Infinite Blades` in a
17-card deck and did 14.8 damage a round. The five that lost held the same
number of real cards, no engine, and did 8.6. These tests pin the two things
that follow from that, and the one trap in between: `ENGINE_TAGS` counts every
Power, so `Footwork` and `Wraith Form` satisfy it while adding no damage at
all, and using the broad set to ask "does this deck own an engine yet?" would
let a Footwork suppress the pull toward the first Infinite Blades.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from bot.game_state import GameState
from bot.run_recorder import RunRecorder
from bot.strategy import cards

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "damage_per_round.py"


def _load_metric():
    spec = importlib.util.spec_from_file_location("dpr_eng", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dpr = _load_metric()

STARTER_DECK = ["Strike"] * 5 + ["Defend"] * 5 + ["Neutralize", "Survivor"]


def offer(*names):
    return [{"index": i, "name": n} for i, n in enumerate(names)]


def picked(cards_, deck, act=1, floor=6):
    index = cards.best_card_reward_index(cards_, deck, act=act, floor=floor)
    return None if index is None else cards_[index]["name"]


# --- what is an engine ------------------------------------------------------

@pytest.mark.parametrize("name,kind", [
    ("Infinite Blades", "self"),      # makes the Shivs it scales
    ("Noxious Fumes", "self"),        # applies the poison it stacks
    ("Serpent Form", "self"),
    ("Caltrops", "self"),             # thorns is damage, every turn, for free
    ("Accuracy", "payoff"),           # does nothing without Shivs
    ("Envenom", "self"),
])
def test_damage_engines_are_recognised(name, kind):
    assert cards.damage_engine_kind(name) == kind


@pytest.mark.parametrize("name", [
    "Footwork",            # Power, Dexterity -- not a point of damage
    "Wraith Form",         # Power, Intangible
    "Well-Laid Plans",     # Power, Retain
    "Tools of the Trade",  # Power, but a card filter
    "Afterimage",          # scales, but scales Block
    "Dagger Throw",        # deals damage once and is gone
    "Strike",
])
def test_cards_that_are_not_damage_engines(name):
    assert cards.damage_engine_kind(name) is None


def test_the_broad_engine_set_disagrees_on_purpose():
    """The trap this whole change rests on, stated as a test.

    If these two ever agree, `DAMAGE_ENGINE_*` has stopped earning its keep.
    """
    tags = set((cards.card_info("Footwork") or {}).get("tags") or [])
    assert tags & cards.ENGINE_TAGS            # broad set: yes, it is an engine
    assert cards.damage_engine_kind("Footwork") is None   # narrow set: no


def test_damage_engines_lists_only_the_real_ones():
    deck = STARTER_DECK + ["Footwork", "Infinite Blades", "Noxious Fumes"]
    assert sorted(cards.damage_engines(deck)) == ["Infinite Blades", "Noxious Fumes"]
    assert cards.damage_engines(STARTER_DECK) == []


# --- the first engine is the pick -------------------------------------------

def test_a_first_engine_beats_a_better_rated_flat_card():
    """The defect, exactly as recommendation 2 described it.

    Backstab is the stronger card on its own rating, and before this change
    the bot took it -- the quality gate judges a card against the median of
    the deck we already hold, which is the one comparison a first engine
    always loses.
    """
    assert picked(offer("Infinite Blades", "Backstab", "Deflect"), STARTER_DECK) == "Infinite Blades"


def test_a_second_engine_does_not_get_the_same_shortcut():
    """The pull is for the *first* engine. After that, normal rules."""
    deck = STARTER_DECK + ["Infinite Blades"]
    assert picked(offer("Caltrops", "Backstab"), deck) == "Backstab"


def test_a_payoff_engine_with_nothing_to_pay_off_is_not_an_engine_yet():
    assert picked(offer("Accuracy", "Backstab"), STARTER_DECK) == "Backstab"


def test_a_payoff_engine_counts_once_the_deck_feeds_it():
    deck = STARTER_DECK + ["Blade Dance", "Cloak and Dagger"]
    assert picked(offer("Accuracy", "Backstab"), deck) == "Accuracy"


def test_an_offer_with_no_engine_at_all_is_judged_normally():
    assert picked(offer("Backstab", "Deflect"), STARTER_DECK) == "Backstab"


def test_the_pull_does_not_reach_below_the_absolute_floor():
    """Wanting an engine badly is not a reason to take a bad one.

    The deck-relative bar is skipped for a first engine; the absolute floor is
    not, so a genuinely weak engine still has to clear it.
    """
    floor_value = cards._absolute_floor(1, 6)
    weak = [n for n in cards.CARDS_BY_NAME
            if cards.damage_engine_kind(n)
            and (cards._base_quality(n) or 0) < floor_value]
    if not weak:
        pytest.skip("no engine in the pool currently rates below the absolute floor")
    assert picked(offer(weak[0]), STARTER_DECK) is None


# --- dilution ---------------------------------------------------------------

def _big_deck():
    deck = STARTER_DECK + ["Backstab", "Dagger Throw", "Pounce", "Flying Knee",
                           "Quick Slash", "Slice", "Bane", "Deflect"]
    assert len(deck) >= cards.ENGINE_DILUTION_DECK
    return deck + ["Infinite Blades"]


def test_a_big_deck_refuses_a_flat_addition():
    assert picked(offer("Deflect", "Slice"), _big_deck()) is None


def test_a_big_deck_still_takes_a_card_that_feeds_its_engine():
    """Shivs make Infinite Blades bigger, so the draw-frequency argument
    does not apply to them."""
    assert picked(offer("Deflect", "Blade Dance"), _big_deck()) == "Blade Dance"


def test_the_damping_is_off_while_the_deck_is_still_small():
    assert cards._dilution_penalty("Deflect", STARTER_DECK) == 0.0


def test_the_damping_never_applies_to_an_engine():
    big = _big_deck()
    assert cards._dilution_penalty("Noxious Fumes", big) == 0.0
    assert cards._dilution_penalty("Deflect", big) < 0.0


def test_a_self_contained_engine_is_fed_by_nothing():
    """Caltrops does not get bigger because the deck got bigger, so a deck
    whose only engine is Caltrops damps everything flat."""
    deck = [n for n in _big_deck() if n != "Infinite Blades"] + ["Caltrops"]
    assert cards._dilution_penalty("Blade Dance", deck) < 0.0


# --- measuring it at floor 17 -----------------------------------------------

def _state(state_type="monster", floor=1, act=1, hp=70, draw=None):
    return GameState({
        "state_type": state_type,
        "run": {"act": act, "floor": floor, "ascension": 0},
        "battle": {"is_play_phase": True, "enemies": []},
        "player": {
            "character": "The Silent", "hp": hp, "max_hp": 70, "gold": 9,
            "potions": [], "max_potion_slots": 3, "relics": [],
            "hand": [], "draw_pile": [{"name": n} for n in (draw or ["Strike"])],
            "discard_pile": [], "exhaust_pile": [],
        },
    })


def test_the_deck_is_recorded_at_the_act_1_boss(tmp_path):
    rec = RunRecorder(tmp_path, "run_test.jsonl")
    rec.observe(_state(floor=1, draw=["Strike", "Defend"]), "choose_map_node", {})
    rec.observe(_state(floor=17, draw=["Strike", "Infinite Blades"]), "play_card", {})
    rec.observe(_state(state_type="game_over", floor=17, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["deck_at_floor"]["a1f17"] == ["Infinite Blades", "Strike"]


def test_only_the_first_clean_reading_on_the_floor_is_kept(tmp_path):
    """A later poll in the same fight can be clean again, by which point the
    deck is not what the run took into the boss."""
    rec = RunRecorder(tmp_path, "run_test.jsonl")
    rec.observe(_state(floor=17, draw=["Strike", "Infinite Blades"]), "play_card", {})
    rec.observe(_state(floor=17, draw=["Shiv", "Shiv"]), "play_card", {})
    rec.observe(_state(state_type="game_over", floor=17, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["deck_at_floor"]["a1f17"] == ["Infinite Blades", "Strike"]


def test_ordinary_floors_are_not_snapshotted(tmp_path):
    rec = RunRecorder(tmp_path, "run_test.jsonl")
    rec.observe(_state(floor=8, draw=["Strike"]), "play_card", {})
    rec.observe(_state(state_type="game_over", floor=8, hp=0), "menu_select", {})

    record = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["deck_at_floor"] == {}


def test_a_run_recorded_before_the_snapshot_existed_reports_no_density():
    """A missing measurement is not a density of 0%, and must not be averaged
    in as one."""
    shape = dpr.boss_shape({"deck": ["Strike"], "floor_reached": 17})
    assert shape["density"] is None
    assert dpr._boss_density_cell([{"boss_density": None}]) == "--"


def test_density_at_floor_17_counts_damage_engines_only():
    run = {"deck_at_floor": {"a1f17": ["Strike"] * 8 + ["Footwork", "Infinite Blades"]}}
    shape = dpr.boss_shape(run)
    assert shape["size"] == 10
    assert shape["engines"] == 1          # Footwork is not one
    assert shape["density"] == pytest.approx(0.1)


def test_the_cell_says_how_many_runs_it_rests_on():
    rows = [{"boss_density": 0.2}, {"boss_density": 0.1}, {"boss_density": None}]
    assert dpr._boss_density_cell(rows) == "15% (2)"
