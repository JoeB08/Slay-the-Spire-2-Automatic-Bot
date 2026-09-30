from bot.game_state import GameState
from bot.strategy import rewards
from conftest import load_fixture


def test_picks_highest_synergy_card_from_real_offer():
    gs = GameState(load_fixture("card_reward.json"))
    action, fields = rewards.decide_card_reward(gs)
    assert action == "select_card_reward"
    offered = {c["index"]: c["name"] for c in gs.card_reward["cards"]}
    # Leading Strike (34% pick rate, shiv) beats Blade Dance (29%) and
    # Anticipate (6%) with an empty deck and no established synergy yet.
    assert offered[fields["card_index"]] == "Leading Strike"


def test_skips_when_nothing_offered():
    gs = GameState({"state_type": "card_reward", "card_reward": {"cards": [], "can_skip": True}})
    action, fields = rewards.decide_card_reward(gs)
    assert action == "skip_card_reward"


def test_claims_reward_items_in_order():
    gs = GameState(
        {
            "state_type": "rewards",
            "rewards": {"items": [{"index": 0, "type": "gold"}, {"index": 1, "type": "card"}], "can_proceed": True},
        }
    )
    action, fields = rewards.decide_rewards(gs)
    assert action == "claim_reward"
    assert fields["index"] == 0


def _rewards_state(items, potions, max_slots=3):
    return GameState(
        {
            "state_type": "rewards",
            "rewards": {"items": items, "can_proceed": True},
            "player": {
                "hp": 50,
                "max_hp": 70,
                "gold": 100,
                "potions": potions,
                "max_potion_slots": max_slots,
            },
        }
    )


def test_skips_potion_reward_when_potion_belt_is_full():
    # Live bug: claiming a potion with a full belt returns "ok" but does
    # nothing, so always taking items[0] looped 90 times.
    gs = _rewards_state(
        items=[{"index": 0, "type": "potion"}, {"index": 1, "type": "card"}],
        potions=[{"id": "A"}, {"id": "B"}, {"id": "C"}],
    )
    action, fields = rewards.decide_rewards(gs)
    assert action == "claim_reward"
    assert fields["index"] == 1  # the card, not the unclaimable potion


def test_discards_worst_potion_to_make_room_for_a_clearly_better_one():
    gs = _rewards_state(
        items=[{"index": 0, "type": "potion", "potion_description": "Deal 30 damage."}],
        potions=[
            {"id": "A", "slot": 0, "description": "Draw 3 cards."},
            {"id": "B", "slot": 1, "description": "Gain 12 Block."},
            {"id": "C", "slot": 2, "description": "Gain 12 Block."},
        ],
    )
    action, fields = rewards.decide_rewards(gs)
    assert action == "discard_potion"
    assert fields["slot"] == 0  # the draw potion is the weakest held


def test_does_not_swap_for_a_marginal_upgrade():
    gs = _rewards_state(
        items=[{"index": 0, "type": "potion", "potion_description": "Gain 12 Block."}],
        potions=[
            {"id": "A", "slot": 0, "description": "Gain 12 Block."},
            {"id": "B", "slot": 1, "description": "Gain 12 Block."},
            {"id": "C", "slot": 2, "description": "Gain 12 Block."},
        ],
    )
    action, _ = rewards.decide_rewards(gs)
    assert action == "proceed"


def test_proceeds_when_only_an_unclaimable_potion_remains():
    gs = _rewards_state(
        items=[{"index": 0, "type": "potion"}],
        potions=[{"id": "A"}, {"id": "B"}, {"id": "C"}],
    )
    action, _ = rewards.decide_rewards(gs)
    assert action == "proceed"


def test_still_claims_potion_when_a_slot_is_free():
    gs = _rewards_state(
        items=[{"index": 0, "type": "potion"}],
        potions=[{"id": "A"}],
    )
    action, fields = rewards.decide_rewards(gs)
    assert action == "claim_reward"
    assert fields["index"] == 0


def test_does_not_reclaim_a_card_reward_it_already_skipped():
    # Live bug: claim_reward opened the card screen, nothing scored well
    # enough so it skipped, which returned to rewards with the card still
    # unclaimed -- 298 iterations of claim -> skip -> claim -> skip.
    rewards._skipped_card_rewards.clear()

    card_gs = GameState(
        {
            "state_type": "card_reward",
            "run": {"act": 1, "floor": 5},
            "card_reward": {
                "cards": [{"index": 0, "name": "Slice"}, {"index": 1, "name": "Sucker Punch"}],
                "can_skip": True,
            },
            "player": {
                "hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3,
                # A deck of real cards, so the two weak offers (Slice 5,
                # Sucker Punch 8) fall below its quality bar and get skipped.
                "hand": [], "discard_pile": [], "exhaust_pile": [],
                "draw_pile": [
                    {"name": "Adrenaline"}, {"name": "Footwork"}, {"name": "Noxious Fumes"},
                ],
            },
        }
    )
    action, _ = rewards.decide_card_reward(card_gs)
    assert action == "skip_card_reward"

    back_on_rewards = GameState(
        {
            "state_type": "rewards",
            "run": {"act": 1, "floor": 5},
            "rewards": {"items": [{"index": 0, "type": "card"}], "can_proceed": True},
            "player": {"hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3},
        }
    )
    action, _ = rewards.decide_rewards(back_on_rewards)
    assert action == "proceed"


def test_skip_memory_is_per_floor():
    rewards._skipped_card_rewards.clear()
    rewards._skipped_card_rewards.add((1, 5))
    next_floor = GameState(
        {
            "state_type": "rewards",
            "run": {"act": 1, "floor": 6},
            "rewards": {"items": [{"index": 0, "type": "card"}], "can_proceed": True},
            "player": {"hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3},
        }
    )
    action, _ = rewards.decide_rewards(next_floor)
    assert action == "claim_reward"  # a new floor's card reward is still fair game


def test_proceeds_once_items_claimed():
    gs = GameState({"state_type": "rewards", "rewards": {"items": [], "can_proceed": True}})
    action, fields = rewards.decide_rewards(gs)
    assert action == "proceed"


def _rewards_at(act, floor, items):
    return GameState({
        "state_type": "rewards",
        "run": {"act": act, "floor": floor, "ascension": 0},
        "rewards": {"items": items, "can_proceed": True},
        "player": {"hp": 50, "max_hp": 70, "gold": 100, "potions": [], "max_potion_slots": 3},
    })


def test_skip_memory_does_not_leak_into_the_next_run():
    """Keyed on (act, floor) alone, a floor-2 skip in one run auto-skipped
    floor 2 in every later run of the session -- without ever opening the card
    screen. Five such cases were logged in a single 8-run set, all at low
    floors that recur every run."""
    from bot.strategy import rewards as rw

    rw._skipped_card_rewards.clear()
    rw._skip_memory_run = None
    card = [{"index": 0, "type": "card"}]

    # Deep in a run, having skipped floor 2 earlier.
    rw.decide_rewards(_rewards_at(1, 12, card))
    rw._skipped_card_rewards.add((1, 2))

    # A new run begins: the floor goes backwards.
    action, _ = rw.decide_rewards(_rewards_at(1, 2, card))
    assert (1, 2) not in rw._skipped_card_rewards, "stale skip carried into a new run"
    assert action == "claim_reward", "should open the card reward on a fresh run"


def test_skip_memory_still_prevents_the_ping_pong_within_one_run():
    """The memory exists for a reason: without it, claim -> skip -> claim
    looped 298 times in a live run."""
    from bot.strategy import rewards as rw

    rw._skipped_card_rewards.clear()
    rw._skip_memory_run = None
    card = [{"index": 0, "type": "card"}]

    rw.decide_rewards(_rewards_at(1, 5, card))
    rw._skipped_card_rewards.add((1, 5))
    action, _ = rw.decide_rewards(_rewards_at(1, 5, card))
    assert action != "claim_reward", "must not reopen a reward it just skipped"
