import copy

from bot.game_state import GameState
from bot.strategy import misc_screens
from conftest import load_fixture


def test_confirms_once_can_confirm_is_true():
    # This exact payload caused a live infinite loop: the code was reading
    # gs.hand instead of hand_select.cards/selected_cards/can_confirm and so
    # never noticed a card was already selected and confirmable.
    gs = GameState(load_fixture("hand_select_discard.json"))
    action, fields = misc_screens.decide_hand_select(gs)
    assert action == "combat_confirm_selection"
    assert fields == {}


def test_selects_a_remaining_card_when_not_yet_confirmable():
    raw = copy.deepcopy(load_fixture("hand_select_discard.json"))
    raw["hand_select"]["can_confirm"] = False
    raw["hand_select"]["selected_cards"] = []
    gs = GameState(raw)
    action, fields = misc_screens.decide_hand_select(gs)
    assert action == "combat_select_card"
    assert fields["card_index"] in {c["index"] for c in raw["hand_select"]["cards"]}


def test_does_not_reselect_an_already_selected_card():
    raw = copy.deepcopy(load_fixture("hand_select_discard.json"))
    raw["hand_select"]["can_confirm"] = False
    raw["hand_select"]["selected_cards"] = [{"index": 1, "name": "Strike"}]
    gs = GameState(raw)
    action, fields = misc_screens.decide_hand_select(gs)
    assert action == "combat_select_card"
    assert fields["card_index"] != 1


def test_card_select_transform_picks_a_weak_card_not_cancel():
    # This exact payload caused a live infinite loop: the code read top-level
    # gs.raw["cards"]/["options"] instead of card_select.cards, saw nothing,
    # and sent cancel_selection forever -- which wasn't even valid here
    # (can_cancel is false in the real payload).
    gs = GameState(load_fixture("card_select_transform.json"))
    action, fields = misc_screens.decide_card_select(gs)
    assert action == "select_card"
    offered = {c["index"]: c["name"] for c in gs.raw["card_select"]["cards"]}
    assert offered[fields["index"]] in ("Strike", "Defend")


def _multi_pick_state(can_confirm=False, names=("Slice", "Deflect", "Footwork", "Adrenaline")):
    return GameState(
        {
            "state_type": "card_select",
            "run": {"act": 1, "floor": 3},
            "card_select": {
                "screen_type": "simple_select",
                "prompt": "Choose 2 Common Cards to Add to Your Deck.",
                "cards": [{"index": i, "id": n.upper(), "name": n} for i, n in enumerate(names)],
                "preview_showing": False,
                "can_cancel": False,
                "can_confirm": can_confirm,
            },
            "player": {
                "hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3,
                "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }
    )


def test_multi_pick_selects_a_different_card_each_time():
    # Live bug: "Choose 2 Common Cards" reports no selected_cards, so the bot
    # re-picked its single best card forever (81 identical select_card calls)
    # and never reached the confirm step.
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()

    gs = _multi_pick_state()
    first_action, first_fields = misc_screens.decide_card_select(gs)
    assert first_action == "select_card"

    second_action, second_fields = misc_screens.decide_card_select(gs)
    assert second_action == "select_card"
    assert second_fields["index"] != first_fields["index"]


def test_multi_pick_confirms_only_once_enough_cards_are_selected():
    """`can_confirm` means confirming is *legal*, not that enough is selected.

    A live "Choose 3 cards to Enchant." screen arrived with `can_confirm:
    True` and nothing selected. Confirming there sent 0 of 3, the game ignored
    it, the state never changed, and the stuck detector relaunched the game --
    twice in a row before this was caught.
    """
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()

    # Prompt asks for 2. After one pick, confirming would be premature.
    first = misc_screens.decide_card_select(_multi_pick_state())
    assert first[0] == "select_card"
    second = misc_screens.decide_card_select(_multi_pick_state(can_confirm=True))
    assert second[0] == "select_card", "should pick the second card, not confirm early"

    action, fields = misc_screens.decide_card_select(_multi_pick_state(can_confirm=True))
    assert action == "confirm_selection"
    assert fields == {}


def test_a_zero_selection_confirm_is_never_sent():
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    state = _multi_pick_state(can_confirm=True)
    state.raw["card_select"]["prompt"] = "Choose 3 cards to Enchant."
    action, _ = misc_screens.decide_card_select(state)
    assert action == "select_card"


def test_pick_memory_resets_when_a_different_screen_appears():
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()

    first = _multi_pick_state()
    a1 = misc_screens.decide_card_select(first)[1]["index"]

    # A brand-new screen with different cards -- the best pick should be
    # available again rather than suppressed by the previous visit's memory.
    other = _multi_pick_state(names=("Slice", "Deflect", "Footwork", "Adrenaline", "Catalyst"))
    other.raw["card_select"]["prompt"] = "Choose 1 card."
    a2 = misc_screens.decide_card_select(other)[1]["index"]
    assert a2 == a1  # same best card, freshly selectable on the new screen


def test_replace_style_prompt_picks_our_worst_card_not_our_best():
    # Gambling Chip: "discard any number of cards, then draw that many". The
    # chosen cards are thrown away, so picking our best card is backwards.
    raw = {
        "state_type": "card_select",
        "run": {"act": 1, "floor": 3},
        "card_select": {
            "screen_type": "simple_select",
            "prompt": "Choose cards to discard, then draw that many.",
            "cards": [
                {"index": 0, "id": "ADRENALINE", "name": "Adrenaline"},
                {"index": 1, "id": "SLICE", "name": "Slice"},
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
    assert offered[fields["index"]] == "Slice"  # the weaker card, not Adrenaline


def test_add_to_deck_prompt_still_picks_our_best_card():
    raw = {
        "state_type": "card_select",
        "run": {"act": 1, "floor": 3},
        "card_select": {
            "screen_type": "simple_select",
            "prompt": "Choose a card to Add to Your Deck.",
            "cards": [
                {"index": 0, "id": "ADRENALINE", "name": "Adrenaline"},
                {"index": 1, "id": "SLICE", "name": "Slice"},
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
    _, fields = misc_screens.decide_card_select(gs)
    offered = {c["index"]: c["name"] for c in raw["card_select"]["cards"]}
    assert offered[fields["index"]] == "Adrenaline"


def test_bundle_select_reads_nested_payload_and_picks_the_better_pack():
    # Live bug: read top-level gs.raw["bundles"] (absent), fell through to
    # cancel_bundle_selection -- which isn't valid here (can_cancel is false)
    # -- and errored in a loop on the Scroll Boxes relic.
    gs = GameState(load_fixture("bundle_select.json"))
    action, fields = misc_screens.decide_bundle_select(gs)
    assert action == "select_bundle"
    # Bundle 0 (Deadly Poison / Blade Dance / Acrobatics) outscores bundle 1
    # (Sucker Punch / Poisoned Stab / Escape Plan) on Silent synergy.
    assert fields["index"] == 0


def test_bundle_select_confirms_once_preview_is_showing():
    raw = copy.deepcopy(load_fixture("bundle_select.json"))
    raw["bundle_select"]["preview_showing"] = True
    gs = GameState(raw)
    action, fields = misc_screens.decide_bundle_select(gs)
    assert action == "confirm_bundle_selection"
    assert fields == {}


def test_bundle_select_does_not_cancel_when_cancel_is_unavailable():
    raw = copy.deepcopy(load_fixture("bundle_select.json"))
    raw["bundle_select"]["bundles"] = []
    raw["bundle_select"]["can_cancel"] = False
    gs = GameState(raw)
    action, _ = misc_screens.decide_bundle_select(gs)
    assert action != "cancel_bundle_selection"


def test_card_select_confirms_once_preview_is_showing():
    # Second half of the same live loop: after select_card, a preview of the
    # transform result appears and needs its own confirm_selection -- without
    # this the bot kept re-selecting the same card forever.
    gs = GameState(load_fixture("card_select_preview.json"))
    action, fields = misc_screens.decide_card_select(gs)
    assert action == "confirm_selection"
    assert fields == {}
