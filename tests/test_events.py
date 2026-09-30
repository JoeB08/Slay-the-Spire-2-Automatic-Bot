from bot.game_state import GameState
from bot.strategy import events


def _event(options, hp=70, max_hp=70):
    return GameState(
        {
            "state_type": "event",
            "run": {"act": 1, "floor": 5},
            "event": {"in_dialogue": False, "options": options},
            "player": {"hp": hp, "max_hp": max_hp, "gold": 100, "potions": [], "max_potion_slots": 3},
        }
    )


def _opt(index, title, description="", **kw):
    return {"index": index, "title": title, "description": description, "is_locked": False,
            "is_proceed": False, "was_chosen": False, **kw}


HP_COST = _opt(0, "Take the shortcut", "Lose 15 HP. Gain 200 Gold.")
SAFE_GOLD = _opt(1, "Search the ruins", "Gain 50 Gold.")
LEAVE = _opt(2, "Leave", "", is_proceed=True)


def test_hp_costs_are_penalised_far_less_when_healthy_than_when_hurt():
    # The heuristic reads HP costs but not payout *amounts* (it can't tell 200
    # gold from 50), so it won't trade HP for a big prize. What it must get
    # right is the relative pressure: the same option is much more acceptable
    # at full HP than at low HP.
    healthy = events._score_option(HP_COST, hp=70, max_hp=70)
    hurt = events._score_option(HP_COST, hp=25, max_hp=70)
    assert healthy > hurt


def test_avoids_the_same_hp_cost_when_low():
    # 15 HP out of 25 is most of what we have left -- take the safe option.
    gs = _event([HP_COST, SAFE_GOLD], hp=25, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_rejects_an_hp_cost_that_would_leave_us_critically_low():
    gs = _event([HP_COST, LEAVE], hp=18, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 2  # bail out rather than drop to 3 HP


def test_never_picks_a_lethal_option_even_if_it_is_the_only_scored_one():
    lethal = _opt(0, "Bleed", "Lose 30 HP. Gain a relic.", relic_name="Something")
    gs = _event([lethal, LEAVE], hp=20, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 2


def test_prefers_healing_when_hurt():
    heal = _opt(0, "Rest by the fire", "Heal 20 HP.")
    gold = _opt(1, "Take the coins", "Gain 50 Gold.")
    gs = _event([heal, gold], hp=20, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 0


def test_percentage_hp_costs_are_understood():
    pct = _opt(0, "Blood price", "Lose 25% of your Max HP. Gain a relic.", relic_name="Thing")
    safe = _opt(1, "Walk away", "Gain 30 Gold.")
    gs = _event([pct, safe], hp=22, max_hp=70)  # 25% of 70 = 17 -> would leave 5
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_takes_cheapest_cost_when_every_option_is_disqualifying():
    big = _opt(0, "Big price", "Lose 30 HP.")
    small = _opt(1, "Small price", "Lose 16 HP.")
    gs = _event([big, small], hp=18, max_hp=70)  # no proceed option offered
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_does_not_take_a_wasted_heal_at_full_hp():
    # Real logged decision (Dense Vegetation, act 1 floor 3, 70/70): it chose
    # "Heal 21 HP. Fight some enemies." over "Gain 66 Gold. Lose 8 HP." The
    # heal restored nothing and cost a fight.
    trudge = _opt(0, "Trudge On", "Gain 66 Gold. Lose 8 HP.")
    rest = _opt(1, "Rest", "Heal 21 HP. Fight some enemies.")
    gs = _event([trudge, rest], hp=70, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 0


def test_takes_the_same_heal_when_actually_hurt():
    trudge = _opt(0, "Trudge On", "Gain 66 Gold. Lose 8 HP.")
    rest = _opt(1, "Rest", "Heal 21 HP. Fight some enemies.")
    gs = _event([trudge, rest], hp=30, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_reward_size_is_read_not_just_the_keyword():
    small = _opt(0, "Pocket change", "Gain 5 Gold.")
    large = _opt(1, "Windfall", "Gain 200 Gold.")
    gs = _event([small, large], hp=70, max_hp=70)
    _, fields = events.decide_event(gs)
    assert fields["index"] == 1


def test_advances_dialogue_when_in_dialogue():
    gs = GameState(
        {
            "state_type": "event",
            "run": {"act": 1, "floor": 5},
            "event": {"in_dialogue": True, "options": []},
            "player": {"hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3},
        }
    )
    action, _ = events.decide_event(gs)
    assert action == "advance_dialogue"


# --- Neow-style relic/card offers -----------------------------------------

def _relic_opt(index, title, description, relic=True):
    return {"index": index, "title": title, "description": description,
            "relic_name": title if relic else None,
            "is_locked": False, "is_proceed": False}


def test_a_curse_bolted_onto_an_offer_is_priced():
    """"Add 1 Injury to your Deck" matched no negative hint and cost exactly
    nothing -- event prose names curses without ever saying "curse"."""
    assert events._added_junk_cost("Add 1 Injury to your Deck.") == events.CURSE_COST
    assert events._added_junk_cost("Add 2 Wounds into your Deck.") == 2 * events.STATUS_COST
    # An unrecognised name costs nothing, so a new curse degrades to today's
    # behaviour rather than mispricing something harmless.
    assert events._added_junk_cost("Add 1 Blessing to your Deck.") == 0.0


def test_card_offers_are_scored_by_rarity_not_by_the_word_card():
    rare = events._card_offer_bonus("Choose 1 of 3 Rare cards to add to your Deck.")
    common = events._card_offer_bonus("Choose 1 of 3 Common cards to add to your Deck.")
    assert rare > common > 0
    assert events._card_offer_bonus("Gain 150 Gold.") == 0.0


def test_three_rare_cards_now_outrank_a_vague_boss_relic_offer():
    """The real Neow screen from a logged run: Hefty Tablet and Lava Rock both
    scored 4.00 and the tie fell to sort order. Three Rare cards minus one
    Injury is plainly the better of the two."""
    tablet = _relic_opt(2, "Hefty Tablet",
                  "Choose 1 of 3 Rare cards to add to your Deck. Add 1 Injury to your Deck.")
    lava = _relic_opt(1, "Lava Rock", "The Act 1 Boss drops 2 Relics.")
    assert events._score_option(tablet, 70, 70, []) > events._score_option(lava, 70, 70, [])
