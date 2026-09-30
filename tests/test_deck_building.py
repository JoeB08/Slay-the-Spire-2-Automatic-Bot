from bot import deck_memory
from bot.game_state import GameState
from bot.strategy import cards as card_db

STARTER_DECK = ["Strike"] * 5 + ["Defend"] * 5 + ["Neutralize", "Survivor"]


def _offer(*names):
    return [{"index": i, "name": n} for i, n in enumerate(names)]


# --- deck memory (the removal bug) -----------------------------------------

def _combat(deck, floor=4):
    return GameState(
        {
            "state_type": "monster", "run": {"act": 1, "floor": floor},
            "battle": {"is_play_phase": True, "enemies": []},
            "player": {
                "hp": 50, "max_hp": 70, "gold": 200, "potions": [], "max_potion_slots": 3,
                "hand": [{"name": n} for n in deck],
                "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def _out_of_combat(state_type="shop", floor=5):
    return GameState(
        {
            "state_type": state_type, "run": {"act": 1, "floor": floor},
            "shop": {"items": []},
            "player": {
                "hp": 50, "max_hp": 70, "gold": 200, "potions": [], "max_potion_slots": 3,
                "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def test_deck_is_remembered_from_combat_into_a_shop():
    deck_memory.remember(_combat(STARTER_DECK))
    shop = _out_of_combat()
    assert shop.full_deck_names() == []  # the piles really are empty here
    assert "Strike" in deck_memory.current_deck(shop)


def test_deck_memory_clears_when_a_new_run_starts():
    deck_memory.remember(_combat(STARTER_DECK, floor=12))
    deck_memory.remember(_out_of_combat(floor=1))  # floor went backwards
    assert deck_memory.current_deck(_out_of_combat(floor=1)) == []


# --- archetype commitment ---------------------------------------------------

def test_no_archetype_until_enough_cards_support_one():
    assert card_db.dominant_archetype(STARTER_DECK) is None


def test_detects_a_poison_archetype():
    deck = STARTER_DECK + ["Deadly Poison", "Noxious Fumes", "Bouncing Flask"]
    assert card_db.dominant_archetype(deck) == "poison"


def test_detects_a_shiv_archetype():
    deck = STARTER_DECK + ["Blade Dance", "Accuracy", "Infinite Blades"]
    assert card_db.dominant_archetype(deck) == "shiv"


def test_act1_commitment_is_soft_and_never_penalises():
    poison_deck = STARTER_DECK + ["Deadly Poison", "Noxious Fumes"]
    # An off-archetype card keeps its value in Act 1 -- the engine isn't settled.
    off = card_db.score_card_for_deck("Blade Dance", poison_deck, act=1)
    neutral = card_db.score_card_for_deck("Blade Dance", STARTER_DECK, act=1)
    assert off >= neutral - 0.01


def test_act2_commitment_is_hard_and_penalises_off_archetype():
    poison_deck = STARTER_DECK + ["Deadly Poison", "Noxious Fumes"]
    on_plan = card_db.score_card_for_deck("Bouncing Flask", poison_deck, act=2)
    off_plan = card_db.score_card_for_deck("Blade Dance", poison_deck, act=2)
    assert on_plan > off_plan


def test_universally_good_cards_escape_the_act2_penalty():
    poison_deck = STARTER_DECK + ["Deadly Poison", "Noxious Fumes"]
    # Adrenaline is energy+draw -- good in any deck, so it must not be
    # penalised just for being off-archetype.
    act1 = card_db.score_card_for_deck("Adrenaline", poison_deck, act=1)
    act2 = card_db.score_card_for_deck("Adrenaline", poison_deck, act=2)
    assert act2 >= act1


# --- skip policy ------------------------------------------------------------

def test_takes_a_card_that_beats_the_current_deck():
    idx = card_db.best_card_reward_index(_offer("Adrenaline", "Slice"), STARTER_DECK, act=1)
    assert idx == 0


def test_skips_when_nothing_beats_the_deck_we_already_have():
    strong_deck = ["Adrenaline", "Afterimage", "Footwork", "Noxious Fumes", "Envenom"]
    idx = card_db.best_card_reward_index(_offer("Slice", "Sucker Punch"), strong_deck, act=1)
    assert idx is None


def test_bar_ignores_junk_so_a_clogged_deck_does_not_lower_standards():
    clean = ["Adrenaline", "Afterimage", "Footwork"]
    clogged = clean + ["Infection"] * 8 + ["Wound"] * 4
    # Junk isn't a yardstick -- the bar must not collapse just because the
    # deck filled up with statuses.
    assert card_db._deck_quality_bar(clogged, None, 1) == card_db._deck_quality_bar(clean, None, 1)


def test_genuinely_weak_cards_are_refused_even_by_a_weak_deck():
    # Speedster (18) was taken in a live run because the deck it was joining
    # was also weak -- exactly when a bad card does the most harm.
    for name in ("Speedster", "Slice", "Sucker Punch", "Anticipate"):
        assert card_db.best_card_reward_index(_offer(name), STARTER_DECK, act=1) is None, name


def test_the_absolute_floor_does_not_block_good_cards():
    assert card_db.best_card_reward_index(_offer("Adrenaline"), STARTER_DECK, act=1) == 0
    assert card_db.best_card_reward_index(_offer("Prepared"), STARTER_DECK, act=1) == 0


def test_archetype_beats_the_sly_nudge_once_committed():
    # Live run: building a shiv deck, it took a Sly card over Blade Dance
    # because Sly's flat bonus outweighed the archetype lean.
    shiv_deck = STARTER_DECK + ["Blade Dance", "Accuracy", "Infinite Blades"]
    assert card_db.dominant_archetype(shiv_deck) == "shiv"
    idx = card_db.best_card_reward_index(_offer("Flick-Flack", "Blade Dance"), shiv_deck, act=1)
    assert idx == 1  # Blade Dance


def test_sly_still_rewarded_when_the_deck_can_trigger_it():
    # NB: the starter deck already contains Survivor ("discard 1 card"), so it
    # counts as having a discard outlet -- compare against one that doesn't.
    no_outlet = ["Strike"] * 5 + ["Defend"] * 5 + ["Neutralize"]
    with_outlet = no_outlet + ["Prepared", "Acrobatics", "Tools of the Trade"]
    assert card_db.deck_tag_counts(no_outlet).get("discard_engine", 0) == 0
    with_engine = card_db.score_card("Tactician", card_db.deck_tag_counts(with_outlet))
    without = card_db.score_card("Tactician", card_db.deck_tag_counts(no_outlet))
    assert with_engine > without


# --- removal targeting ------------------------------------------------------

def test_removal_prompt_cuts_a_starter_not_a_real_card():
    """Live payload where the bot removed Piercing Wail while four Strikes,
    four Defends and a Clumsy curse were on offer."""
    from bot.strategy import misc_screens

    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    names = [
        "Clumsy", "Strike+", "Strike", "Strike", "Strike", "Defend", "Defend", "Defend",
        "Defend", "Neutralize", "Survivor", "Piercing Wail", "Backstab+", "Fan of Knives",
        "Burst+", "Dagger Throw",
    ]
    gs = GameState(
        {
            "state_type": "card_select", "run": {"act": 1, "floor": 10},
            "card_select": {
                "screen_type": "select", "prompt": "Choose a card to Remove.",
                "cards": [{"index": i, "name": n} for i, n in enumerate(names)],
                "preview_showing": False, "can_cancel": False, "can_confirm": False,
            },
            "player": {
                "hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3,
                "hand": [], "draw_pile": [{"name": n} for n in names],
                "discard_pile": [], "exhaust_pile": [],
            },
        }
    )
    action, fields = misc_screens.decide_card_select(gs)
    assert action == "select_card"
    assert names[fields["index"]] in ("Strike", "Defend", "Clumsy")


# --- Grand Finale -----------------------------------------------------------

def test_takes_grand_finale_into_a_deck_small_enough_to_empty():
    small_deck = ["Adrenaline", "Afterimage", "Footwork", "Noxious Fumes", "Envenom"]
    idx = card_db.best_card_reward_index(_offer("Slice", "Grand Finale"), small_deck, act=2)
    assert idx == 1


def test_refuses_grand_finale_when_the_deck_is_too_big_to_empty():
    # It has the worst pick rate in the pool (7%) precisely because it's dead
    # weight in a normal-sized deck -- taking it "because it's a build-around"
    # is the trap.
    big_deck = STARTER_DECK + ["Adrenaline", "Footwork", "Prepared", "Backflip", "Haze"]
    assert len(big_deck) > card_db.GRAND_FINALE_MAX_DECK
    idx = card_db.best_card_reward_index(_offer("Grand Finale"), big_deck, act=2)
    assert idx is None


def test_grand_finale_still_loses_to_a_better_card_in_a_big_deck():
    big_deck = STARTER_DECK + ["Footwork", "Prepared", "Backflip", "Haze"]
    idx = card_db.best_card_reward_index(_offer("Grand Finale", "Adrenaline"), big_deck, act=2)
    assert idx == 1  # Adrenaline, judged on merit


def test_stops_adding_cards_once_grand_finale_is_in_the_deck():
    # Grand Finale needs an empty draw pile, so every extra card works against
    # the win condition -- take nothing, however good it looks.
    deck = STARTER_DECK + ["Grand Finale"]
    assert card_db.best_card_reward_index(_offer("Adrenaline", "Afterimage"), deck, act=2) is None
