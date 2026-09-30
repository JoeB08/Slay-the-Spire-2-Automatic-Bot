from bot.strategy import cards


def test_known_card_lookup():
    info = cards.card_info("Envenom")
    assert info is not None
    assert "poison" in info["tags"]


def test_multiplayer_only_cards_are_penalized_for_solo_play():
    solo_relevant = cards.score_card("Noxious Fumes", {})
    multiplayer_only = cards.score_card("Sneaky", {})
    assert solo_relevant > multiplayer_only


def test_sly_discard_synergy_boosts_score():
    baseline = cards.score_card("Tactician", {})  # sly card, no discard engine established
    with_discard_engine = cards.score_card("Tactician", {"discard_engine": 2})
    assert with_discard_engine > baseline


def test_poison_payoff_boosted_by_existing_poison():
    baseline = cards.score_card("Accelerant", {})
    with_poison = cards.score_card("Accelerant", {"poison": 3})
    assert with_poison > baseline


def test_unknown_card_scores_middling_not_junk():
    """An unfamiliar card is *unknown*, not *bad*.

    Scoring it 0.0 conflated the two: it became the first thing discarded and
    could never be taken as a reward -- the same shape as the bug where
    upgraded names ("Afterimage+") matched nothing. A newly unlocked Act
    brings cards the data file has never seen, so this is the difference
    between adapting and ignoring them.
    """
    unknown = cards.score_card("Definitely Not A Real Card", {})
    assert unknown == cards.UNKNOWN_CARD_QUALITY
    assert 0 < unknown < cards.score_card("Afterimage", {})


def test_quest_cards_are_never_removed_from_the_deck():
    """"Spoils Map" is a reward marker -- "Marks a site of 600 extra Gold in
    the next Act". Cutting it throws the reward away, so it must rank below
    every genuine card as a removal target."""
    quest = {"name": "Spoils Map", "type": "Quest"}
    deck = ["Spoils Map", "Strike", "Afterimage"]
    assert cards.removal_priority(quest, deck) < cards.removal_priority(
        {"name": "Afterimage", "type": "Skill"}, deck
    )
    assert cards.removal_priority(quest, deck) < cards.removal_priority(
        {"name": "Strike", "type": "Attack"}, deck
    )


# --- intrinsic value ---------------------------------------------------

def test_card_stats_are_blended_into_quality():
    """Pick rate says how often people take a card, not what it does. A
    starter Strike (default 30) outscored Pounce+ (20 damage, rated 25) and
    Precise Cut+ (16 damage for 0 energy, rated 19), so the bot skipped all
    three from a bare starter deck on floor 2."""
    assert cards.intrinsic_value("Pounce+") > cards.intrinsic_value("Strike")
    starters = ["Strike"] * 5 + ["Defend"] * 5 + ["Neutralize", "Survivor"]
    offer = [{"index": 0, "name": "Precise Cut+"},
             {"index": 1, "name": "Poisoned Stab+"},
             {"index": 2, "name": "Pounce+"}]
    assert cards.best_card_reward_index(offer, starters, act=1, floor=2) is not None


def test_gated_damage_is_not_credited():
    """Grand Finale reads "Deal 60 damage" but only with an empty draw pile;
    taken at face value it scored 180 and passed every gate."""
    assert cards.intrinsic_value("Grand Finale") == 0.0


def test_variable_damage_is_discounted_not_zeroed():
    # "Deals 2 less damage for each other card in your Hand" -- real, but
    # rarely the printed maximum.
    assert 0 < cards.intrinsic_value("Precise Cut+") < 13 * 3 * 1.3


def test_the_blend_never_lowers_a_card():
    """Blending down punished cards whose worth is real but unparseable --
    Hand Trick (pick rate 31, only 7 Block in its text) stopped being taken."""
    for name in ("Hand Trick", "Blur", "Footwork", "Adrenaline"):
        info = cards.card_info(name) or {}
        pick = float(info.get("pick_rate") or 30)
        assert cards._blend_quality(name, pick) >= pick
