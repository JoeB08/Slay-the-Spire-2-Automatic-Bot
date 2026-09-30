from bot.game_state import GameState
from bot.strategy import rest

REST = {"index": 0, "type": "rest", "name": "Rest", "description": "Heal 30% of your Max HP."}
UPGRADE = {"index": 1, "type": "smith", "name": "Smith", "description": "Upgrade a card."}


def _state(hp, max_hp=70, options=(REST, UPGRADE), deck=("Strike", "Deadly Poison")):
    return GameState(
        {
            "state_type": "rest_site",
            "run": {"act": 1, "floor": 10},
            "rest_site": {"options": [dict(o) for o in options]},
            "player": {
                "hp": hp, "max_hp": max_hp, "gold": 100, "potions": [], "max_potion_slots": 3,
                "hand": [], "draw_pile": [{"name": n} for n in deck],
                "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def _chosen_kind(gs, fields):
    option = next(o for o in gs.rest_site["options"] if o["index"] == fields["index"])
    return rest._option_kind(option)


def test_upgrades_rather_than_resting_for_a_trivial_heal():
    # 66/70: a "30% of max HP" rest is capped by the 4 HP missing, so resting
    # returns almost nothing while the upgrade is permanent.
    gs = _state(hp=66)
    action, fields = rest.decide_rest(gs)
    assert action == "choose_rest_option"
    assert _chosen_kind(gs, fields) == "upgrade"


def test_rests_when_genuinely_low_and_the_heal_is_substantial():
    # 40% is the threshold; 43% now upgrades instead (deck quality compounds,
    # a heal does not), so "genuinely low" means below it.
    gs = _state(hp=26)  # 37% HP, 21 HP back
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "rest"


def test_upgrades_just_above_the_heal_threshold():
    gs = _state(hp=30)  # 43% -- above the line, so the permanent gain wins
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "upgrade"


def test_upgrades_when_reasonably_healthy_even_though_the_heal_is_large():
    # 40/70 is 57% -- the heal would return 21, but an upgrade is permanent
    # and compounds. A 10-run sample rested 22 times and upgraded 0 times
    # because any decent heal beat upgrading; that ordering is now reversed.
    gs = _state(hp=40)
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "upgrade"


def test_rests_when_badly_hurt_even_if_an_upgrade_is_available():
    gs = _state(hp=15)
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "rest"


def test_heal_gain_is_capped_by_missing_hp():
    gs = _state(hp=66)
    assert rest.rest_hp_gain(dict(REST), gs) == 4
    gs_full = _state(hp=70)
    assert rest.rest_hp_gain(dict(REST), gs_full) == 0


def test_explicit_heal_amount_is_read_from_the_option_text():
    gs = _state(hp=40)
    flat = {"index": 0, "type": "rest", "name": "Rest", "description": "Heal 5 HP."}
    assert rest.rest_hp_gain(flat, gs) == 5


def test_small_flat_heal_loses_to_an_upgrade():
    # The reported case: resting for 5 instead of upgrading.
    flat = {"index": 0, "type": "rest", "name": "Rest", "description": "Heal 5 HP."}
    gs = _state(hp=50, options=(flat, UPGRADE))
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "upgrade"


def test_rests_when_there_is_nothing_to_upgrade():
    gs = _state(hp=66, options=(REST,))
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "rest"


REMOVE = {"index": 2, "type": "toke", "name": "Toke", "description": "Remove a card from your Deck."}


def test_relic_granted_removal_option_beats_rest_and_upgrade():
    # Relics add options the decision previously ignored entirely. Deleting a
    # starter is worth more to a Silent deck than a heal or one upgrade.
    gs = _state(hp=40, options=(REST, UPGRADE, REMOVE))
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "remove"


def test_removal_option_ignored_when_there_is_no_junk_to_cut():
    gs = _state(hp=40, options=(REST, UPGRADE, REMOVE), deck=("Adrenaline", "Footwork"))
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) != "remove"


def test_disabled_options_are_ignored():
    disabled = dict(UPGRADE, is_enabled=False)
    gs = _state(hp=66, options=(REST, disabled))
    _, fields = rest.decide_rest(gs)
    assert _chosen_kind(gs, fields) == "rest"


def test_proceeds_when_no_options_are_offered():
    gs = _state(hp=50, options=())
    action, _ = rest.decide_rest(gs)
    assert action == "proceed"
