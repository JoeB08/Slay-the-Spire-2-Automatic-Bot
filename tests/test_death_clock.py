"""The Act 2 boss kills on a timer, not with damage.

The Insatiable has 321 HP and applies Sandpit -- "In 4 turns, you will be
eaten and die" -- while handing out Frantic Escape: "Get farther away.
Increase Sandpit by 1. Increase the cost of this card by 1." The HP total is
bait; the fight is a survival puzzle. A run reached floor 33 and was eaten
while attacking into it.

Frantic Escape is a *Status* card, so the hand-clog rule would otherwise rank
it as dead weight and discard the only answer.
"""
from __future__ import annotations

from bot.game_state import GameState
from bot.strategy import combat


ESCAPE = {"name": "Frantic Escape", "cost": "1", "type": "Status",
          "can_play": True, "index": 0,
          "description": "Get farther away. Increase Sandpit by 1. "
                         "Increase the cost of this card by 1.",
          "target_type": "Self"}
STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "can_play": True,
          "index": 1, "description": "Deal 6 damage.", "target_type": "AnyEnemy"}
SOOT = {"name": "Soot", "cost": "0", "type": "Status", "can_play": False,
        "index": 2, "description": "Unplayable.", "target_type": "Self"}


def _state(sandpit, hand=(ESCAPE, STRIKE, SOOT)):
    status = []
    if sandpit is not None:
        status.append({"name": "Sandpit", "amount": sandpit, "type": "Buff",
                       "text": "In 4 turns, you will be eaten and die."})
    return GameState({
        "state_type": "boss",
        "run": {"act": 2, "floor": 33, "ascension": 0},
        "player": {"hp": 50, "max_hp": 70, "energy": 3, "block": 0, "status": [],
                   "potions": [], "hand": [dict(c) for c in hand], "draw_pile": [],
                   "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{
            "entity_id": "THE_INSATIABLE_0", "name": "The Insatiable",
            "hp": 321, "max_hp": 321, "block": 0, "status": status,
            "intents": [{"type": "Attack", "label": "12", "title": "", "description": ""}]}]},
    })


def _played(gs):
    action, fields = combat.decide(gs)
    if action != "play_card":
        return action
    return next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])


def test_the_clock_is_detected():
    name, turns = combat._death_clock(_state(4).enemies)
    assert name == "Sandpit" and turns == 4


def test_no_clock_when_the_status_is_absent():
    assert combat._death_clock(_state(None).enemies) is None


BIG_HIT = {"name": "Murder+", "cost": "2", "type": "Attack", "can_play": True,
           "index": 3, "description": "Deal 20 damage.", "target_type": "AnyEnemy"}
POWER = {"name": "Footwork+", "cost": "1", "type": "Power", "can_play": True,
         "index": 4, "description": "Gain 3 Dexterity.", "target_type": "Self"}


def test_the_escape_displaces_a_weak_play():
    """A basic Strike into a 321 HP boss is worth less than a turn alive."""
    assert _played(_state(4)) == "Frantic Escape"


def test_but_never_displaces_real_damage_while_the_clock_has_slack():
    assert _played(_state(4, hand=(ESCAPE, BIG_HIT))) == "Murder+"


def test_and_never_displaces_a_power_while_the_clock_has_slack():
    assert _played(_state(4, hand=(ESCAPE, POWER))) == "Footwork+"


def test_but_the_last_turn_outranks_everything():
    """With the clock about to run out, nothing else matters."""
    assert _played(_state(1, hand=(ESCAPE, BIG_HIT))) == "Frantic Escape"
    assert _played(_state(1, hand=(ESCAPE, POWER))) == "Frantic Escape"


def test_the_escape_card_is_not_counted_as_hand_clog():
    gs = _state(4)
    assert combat._dead_cards_in_hand(gs.hand, gs.enemies) == 1  # Soot only


def test_the_escape_card_is_never_discarded():
    from bot.strategy import cards as card_db
    from bot.strategy import misc_screens as m
    gs = _state(2)
    counts = card_db.deck_tag_counts([c["name"] for c in gs.hand])
    ranks = {c["name"]: m._discard_rank(c, counts, 3, gs.enemies, 0, 0, 0, gs.hand)
             for c in gs.hand}
    assert min(ranks, key=lambda n: ranks[n]) == "Frantic Escape"


def test_malaise_plus_is_recognised_as_a_weak_card():
    r""""Apply X+1 Weak" matched neither \d+ nor X, so Malaise+ was not
    treated as a Weak card and got pointed at the lowest-HP enemy."""
    malaise = {"name": "Malaise+", "cost": "X", "type": "Skill", "can_play": True,
               "description": "Enemy loses X+1 Strength. Apply X+1 Weak. "
                              "Gain 2 Tainted. Exhaust.",
               "target_type": "AnyEnemy"}
    assert combat._card_applies_weak(malaise) is True
    small = {"entity_id": "S", "name": "Small", "hp": 12, "max_hp": 40, "block": 0,
             "status": [], "intents": [{"type": "Attack", "label": "3",
                                        "title": "", "description": ""}]}
    big = {"entity_id": "B", "name": "Big", "hp": 132, "max_hp": 132, "block": 0,
           "status": [], "intents": [{"type": "Attack", "label": "11",
                                      "title": "", "description": ""}]}
    assert combat._choose_target(malaise, [small, big])["entity_id"] == "B"


# --- Knowledge Demon: "choose a card" where every option is a penalty -------

class TestPenaltyChoices:
    """The other Act 2 boss forces a permanent penalty every few turns.

    All options score 0 block and 0 damage, so the mid-combat ranking tied and
    `max()` took whatever the game listed first. A live run took
    Disintegration twice -- 13 self-damage a turn -- and died at floor 33.
    """

    DISINTEGRATION = {"name": "Disintegration",
                      "description": "At the end of your turn, take 6 damage."}
    MIND_ROT = {"name": "Mind Rot", "description": "Draw 1 fewer card each turn."}
    SLOTH = {"name": "Sloth",
             "description": "You cannot play more than 3 cards each turn."}

    def _deck(self, block_cards):
        return GameState({
            "state_type": "boss",
            "run": {"act": 2, "floor": 33, "ascension": 0},
            "player": {"hp": 50, "max_hp": 70, "energy": 3, "block": 0,
                       "status": [], "potions": [], "hand": block_cards,
                       "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                       "discard_pile_count": 0, "exhaust_pile_count": 0},
            "battle": {"enemies": [{
                "entity_id": "E", "name": "Knowledge Demon", "hp": 379,
                "max_hp": 379, "block": 0, "status": [],
                "intents": [{"type": "Attack", "label": "19",
                             "title": "", "description": ""}]}]},
        })

    NO_BLOCK = [{"name": "Strike", "type": "Attack", "cost": "1",
                 "description": "Deal 6 damage."}]
    ALL_BLOCK = [{"name": "Defend+", "type": "Skill", "cost": "1",
                  "description": "Gain 8 Block."} for _ in range(5)]

    def test_mind_rot_is_the_default_pick(self):
        gs = self._deck(self.NO_BLOCK)
        assert (combat._penalty_cost(self.MIND_ROT, gs)
                < combat._penalty_cost(self.DISINTEGRATION, gs))

    def test_a_play_cap_still_beats_unblockable_bleed(self):
        gs = self._deck(self.NO_BLOCK)
        assert (combat._penalty_cost(self.SLOTH, gs)
                < combat._penalty_cost(self.DISINTEGRATION, gs))

    def test_but_bleed_wins_when_the_deck_really_blocks(self):
        """"The bot should choose damage if it feels like it has enough block."""
        gs = self._deck(self.ALL_BLOCK)
        assert combat._block_per_turn(gs) > 0, "block capacity must be measurable"
        assert (combat._penalty_cost(self.DISINTEGRATION, gs)
                < combat._penalty_cost(self.MIND_ROT, gs))

    def test_block_per_turn_is_actually_wired(self):
        """It silently returned 0.0 while reading a non-existent attribute."""
        assert combat._block_per_turn(self._deck(self.ALL_BLOCK)) > 10
        assert combat._block_per_turn(self._deck(self.NO_BLOCK)) == 0

    def test_ordinary_cards_are_not_penalties(self):
        assert combat._penalty_cost({"name": "Strike",
                                     "description": "Deal 6 damage."}) == 0.0
