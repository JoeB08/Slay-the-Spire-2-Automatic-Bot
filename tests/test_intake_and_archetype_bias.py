"""Card intake pacing and archetype selection bias.

Two live observations:
  * too few cards taken early, and no slowdown later in the run;
  * almost every run committed to discard, skipping early shiv/poison.
"""
from bot.strategy import cards as card_db

STARTER = ["Strike"] * 5 + ["Defend"] * 5 + ["Neutralize", "Survivor"]


def _offer(*names):
    return [{"index": i, "name": n} for i, n in enumerate(names)]


# --- intake pacing ---------------------------------------------------------

def test_take_bar_rises_as_the_run_progresses():
    early = card_db._progress_bar_factor(1, 1)
    mid = card_db._progress_bar_factor(2, 8)
    late = card_db._progress_bar_factor(3, 12)
    assert early < mid < late
    assert early < 1.0 < late  # generous early, strict late


def test_takes_a_mediocre_card_early_that_it_would_refuse_late():
    # Compared against a realistic act-3 deck: thinned, mostly real cards.
    # (A deck *still* full of starters in act 3 should keep taking cards, and
    # does -- the bar is deck-relative by design.)
    late_deck = ["Backflip", "Prepared", "Footwork", "Adrenaline", "Acrobatics",
                 "Blade Dance", "Leg Sweep", "Escape Plan", "Strike", "Defend"]
    card = "Dagger Throw"  # decent, not exciting
    early = card_db.best_card_reward_index(_offer(card), STARTER, act=1, floor=1)
    late = card_db.best_card_reward_index(_offer(card), late_deck, act=3, floor=12)
    assert early == 0, "should build the deck early"
    assert late is None, "should protect a good deck late"


def test_same_deck_gets_pickier_purely_because_the_run_progressed():
    # Deck held fixed; only run progress differs. Hand Trick sits between the
    # early bar (~30) and the late bar (~67) for this deck.
    deck = ["Backflip", "Prepared", "Footwork", "Adrenaline", "Strike", "Defend"]
    card = "Hand Trick"
    assert card_db.best_card_reward_index(_offer(card), deck, act=1, floor=1) == 0
    assert card_db.best_card_reward_index(_offer(card), deck, act=3, floor=14) is None


def test_a_weak_synergy_pick_does_not_veto_the_whole_offer():
    """Live bug: synergy chose the candidate, then the quality gate judged
    *that* card. Flick-Flack (pick rate 19) out-scored Backstab (43) on
    stacking damage tags, failed the absolute floor, and took the entire
    reward down with it -- the bot skipped both of its first two offers.
    Gating now happens before selection.
    """
    offer = _offer("Backstab", "Flick-Flack", "Sucker Punch")
    idx = card_db.best_card_reward_index(offer, STARTER, act=1, floor=2)
    assert idx is not None, "should not skip an offer containing a good card"
    assert offer[idx]["name"] == "Backstab"


def test_second_real_offer_that_was_skipped_is_now_taken():
    offer = _offer("Flick-Flack", "Leading Strike", "Dagger Spray")
    idx = card_db.best_card_reward_index(offer, STARTER, act=1, floor=5)
    assert idx is not None
    assert offer[idx]["name"] == "Leading Strike"


def test_offer_of_only_weak_cards_is_still_skipped():
    assert card_db.best_card_reward_index(
        _offer("Slice", "Sucker Punch", "Anticipate"), STARTER, act=1, floor=2
    ) is None


def test_absolute_quality_floor_still_applies_early():
    # Taking more early must not mean taking genuinely bad cards.
    assert card_db.best_card_reward_index(_offer("Speedster"), STARTER, act=1, floor=1) is None


# --- archetype bias --------------------------------------------------------

def test_starter_survivor_does_not_seed_a_discard_build():
    # Survivor ships in the starting deck and is tagged as a discard engine,
    # which gave discard a free one-card head start every single run.
    assert card_db.dominant_archetype(STARTER) is None
    # One discard pick alone must not be enough to commit.
    assert card_db.dominant_archetype(STARTER + ["Prepared"]) is None


def test_one_of_each_does_not_commit_to_discard():
    # Previously the starting Survivor broke this tie in discard's favour.
    assert card_db.dominant_archetype(STARTER + ["Blade Dance", "Prepared"]) is None


def test_each_archetype_can_still_be_committed_to():
    assert card_db.dominant_archetype(STARTER + ["Blade Dance", "Leading Strike"]) == "shiv"
    assert card_db.dominant_archetype(STARTER + ["Deadly Poison", "Bouncing Flask"]) == "poison"
    assert card_db.dominant_archetype(STARTER + ["Prepared", "Acrobatics"]) == "discard"


def test_shiv_and_poison_are_favoured_over_discard_when_level():
    # Shiv and poison close fights faster, which is what matters at low
    # ascension. The prior only breaks otherwise-level cases.
    assert card_db.ARCHETYPE_PRIOR["shiv"] > card_db.ARCHETYPE_PRIOR["discard"]
    assert card_db.ARCHETYPE_PRIOR["poison"] > card_db.ARCHETYPE_PRIOR["discard"]
