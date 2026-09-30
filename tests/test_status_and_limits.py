"""Unblockable HP loss, purging held Status cards, and capped turns."""
from __future__ import annotations

from bot.game_state import GameState
from bot.strategy import combat


def _card(**kw):
    kw.setdefault("can_play", True)
    kw.setdefault("target_type", "Self")
    return kw


BECKON = _card(name="Beckon", cost="1", type="Status",
               description="At the end of your turn, if this is in your Hand, lose 6 HP.")
INFECTION = _card(name="Infection", cost="1", type="Status", can_play=False,
                  description="Unplayable. At the end of your turn, if this is "
                              "in your Hand, take 3 damage.")
DEFEND = _card(name="Defend", cost="1", type="Skill", description="Gain 5 Block.")
SURVIVOR = _card(name="Survivor", cost="1", type="Skill",
                 description="Gain 8 Block. Discard 1 card.")
SHIV = _card(name="Shiv", cost="0", type="Attack", description="Deal 4 damage.",
             target_type="AnyEnemy")
STRIKE = _card(name="Strike", cost="1", type="Attack", description="Deal 6 damage.",
               target_type="AnyEnemy")


def _state(hand, status=(), hp=40, energy=3, incoming=10, enemy_hp=60):
    cards = [dict(c, index=i) for i, c in enumerate(hand)]
    return GameState({
        "state_type": "monster",
        "run": {"act": 1, "floor": 12, "ascension": 0},
        "player": {"hp": hp, "max_hp": 70, "energy": energy, "block": 0,
                   "status": list(status), "potions": [], "hand": cards,
                   "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{
            "entity_id": "E0", "name": "Soul Fysh", "hp": enemy_hp, "max_hp": 160,
            "block": 0, "status": [],
            "intents": [{"type": "Attack", "label": str(incoming),
                         "title": "", "description": ""}]}]},
    })


def _played(gs):
    action, fields = combat.decide(gs)
    if action != "play_card":
        return action
    return next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])


class TestUnblockableHpLoss:
    def test_lose_hp_is_unblockable_but_take_damage_is_not(self):
        assert combat._unblockable_self_damage(BECKON) == 6
        assert combat._unblockable_self_damage(INFECTION) == 0
        assert combat._self_damage_in_hand(INFECTION) == 3

    def test_hand_hp_loss_sums_only_the_unblockable_part(self):
        hand = [BECKON, dict(BECKON), INFECTION]
        assert combat._hand_hp_loss(hand) == 12
        assert combat._hand_curse_damage(hand) == 15


class TestPurgingHeldStatusCards:
    def test_beckon_is_played_away_rather_than_blocked_against(self):
        """Block cannot answer "lose 6 HP"; playing it deletes the damage."""
        assert _played(_state([BECKON, DEFEND, DEFEND])) == "Beckon"

    def test_but_not_at_the_cost_of_surviving_the_turn(self):
        assert _played(_state([BECKON, DEFEND], hp=9, energy=1, incoming=10)) == "Defend"

    def test_an_unplayable_status_cannot_be_purged(self):
        assert _played(_state([INFECTION, DEFEND, STRIKE])) != "Infection"


class TestCappedTurns:
    RINGING_BY_NAME = [{"id": "RINGING", "name": "Ringing", "amount": 1}]
    RINGING_BY_TEXT = [{"id": "ODD", "name": "Odd Effect",
                        "description": "You can only play 1 card this turn."}]

    def test_detected_by_name_and_by_text(self):
        assert combat._play_limit(_state([DEFEND], self.RINGING_BY_NAME)) == 1
        assert combat._play_limit(_state([DEFEND], self.RINGING_BY_TEXT)) == 1
        assert combat._play_limit(_state([DEFEND])) is None

    def test_spends_the_single_play_on_the_biggest_block(self):
        hand = [SHIV, SHIV, SURVIVOR, DEFEND]
        assert _played(_state(hand, self.RINGING_BY_NAME)) == "Survivor"
        assert _played(_state(hand, self.RINGING_BY_TEXT)) == "Survivor"

    def test_never_wastes_the_turn_on_a_shiv(self):
        assert _played(_state([SHIV, SHIV, STRIKE], self.RINGING_BY_NAME)) == "Strike"

    def test_without_ringing_the_free_shiv_is_still_correct(self):
        assert _played(_state([SHIV, SHIV, SURVIVOR, DEFEND])) == "Shiv"

    def test_a_lethal_kill_still_outranks_blocking(self):
        gs = _state([SHIV, SHIV, SURVIVOR, STRIKE], self.RINGING_BY_NAME, enemy_hp=5)
        assert _played(gs) == "Strike"


class TestDiscardingSelfDamagingCards:
    """The discard ranker has to agree with the purge step, not fight it."""

    COUNTS_DECK = ["Strike", "Defend"]

    def _rank(self, card, hand):
        from bot.strategy import cards as card_db
        from bot.strategy import misc_screens as m
        counts = card_db.deck_tag_counts(self.COUNTS_DECK)
        return m._discard_rank(card, counts, 3, [], 0, 0, 0, hand)

    def test_pitches_the_one_that_cannot_be_played_away(self):
        """Beckon is playable, so a single energy deletes it. Infection is
        Unplayable and discarding is its only outlet -- so the discard goes
        to Infection even though Beckon costs more HP.
        """
        hand = [BECKON, INFECTION]
        worst = max(hand, key=lambda c: self._rank(c, hand))
        assert worst["name"] == "Infection"

    def test_magnitude_still_decides_between_two_unplayable_ones(self):
        big = dict(INFECTION, name="Infection+",
                   description="Unplayable. At the end of your turn, if this "
                               "is in your Hand, take 7 damage.")
        hand = [INFECTION, big]
        worst = max(hand, key=lambda c: self._rank(c, hand))
        assert worst["name"] == "Infection+"
