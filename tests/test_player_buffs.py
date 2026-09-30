"""Our own Strength and damage multipliers, which the bot could not read.

`_player_dexterity` scaled Block from the very start, but there was no
equivalent for damage. With -6 Strength a 6-damage Strike was still valued at
6 when it lands for 0, so the bot burned energy on dead attacks and claimed
lethals that could not happen; with Double Damage up it passed up kills it
could actually make.
"""
from __future__ import annotations

from bot.game_state import GameState
from bot.strategy import combat


STRIKE = {"name": "Strike", "cost": "1", "type": "Attack", "can_play": True,
          "description": "Deal 6 damage.", "target_type": "AnyEnemy"}
DEFEND = {"name": "Defend", "cost": "1", "type": "Skill", "can_play": True,
          "description": "Gain 5 Block.", "target_type": "Self"}
NEUTRALIZE = {"name": "Neutralize", "cost": "0", "type": "Attack", "can_play": True,
              "description": "Deal 3 damage. Apply 1 Weak.", "target_type": "AnyEnemy"}

STRENGTH_DOWN = [{"id": "STRENGTH_POWER", "name": "Strength", "amount": -6,
                  "type": "Debuff", "description": "Decreases attack damage by 6."}]
DOUBLE_DAMAGE = [{"id": "DOUBLE_DAMAGE", "name": "Double Damage", "amount": 1,
                  "type": "Buff", "description": "Double your damage this turn."}]
SHADOWMELD = [{"id": "SHADOWMELD", "name": "Shadowmeld", "amount": 1, "type": "Buff",
               "description": "Double your Block gain this turn."}]


def _state(status=(), enemy_hp=40, hand=(STRIKE, DEFEND)):
    cards = [dict(c, index=i) for i, c in enumerate(hand)]
    return GameState({
        "state_type": "monster",
        "run": {"act": 1, "floor": 9, "ascension": 0},
        "player": {"hp": 50, "max_hp": 70, "energy": 3, "block": 0,
                   "status": list(status), "potions": [], "hand": cards,
                   "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
                   "discard_pile_count": 0, "exhaust_pile_count": 0},
        "battle": {"enemies": [{
            "entity_id": "E", "name": "Foo", "hp": enemy_hp, "max_hp": 40,
            "block": 0, "status": [],
            "intents": [{"type": "Attack", "label": "5",
                         "title": "", "description": ""}]}]},
    })


# The game applies Strength to the card text before we ever see it: at -2 a
# Strike reports "Deal 4 damage", at -6 "Deal 0 damage". So these use the text
# the game would actually send, and the bot must NOT subtract Strength again.
ZEROED_STRIKE = dict(STRIKE, description="Deal 0 damage.")
ZEROED_NEUTRALIZE = dict(NEUTRALIZE, description="Deal 0 damage. Apply 1 Weak.")


class TestStrength:
    def test_the_status_is_read(self):
        assert combat._player_strength(_state(STRENGTH_DOWN)) == -6

    def test_strength_is_not_applied_twice(self):
        """"Deal 4 damage" at Strength -2 means 4, not 2."""
        gs = _state(STRENGTH_DOWN)
        weakened = dict(STRIKE, description="Deal 4 damage.")
        assert combat._effective_damage(weakened, gs.enemies[0], 0, gs.hand, -2) == 4

    def test_an_attack_zeroed_by_strength_is_not_played(self):
        gs = _state(STRENGTH_DOWN, hand=(ZEROED_STRIKE, DEFEND))
        assert "Strike" not in [c["name"] for c in combat._playable_hand(gs)]

    def test_but_a_debuff_attack_is_still_worth_playing(self):
        """Neutralize still applies Weak even when its damage is zeroed."""
        gs = _state(STRENGTH_DOWN, hand=(ZEROED_NEUTRALIZE, DEFEND))
        assert "Neutralize" in [c["name"] for c in combat._playable_hand(gs)]

    def test_shivs_still_get_strength_applied(self):
        """Shivs are generated, not printed, so their damage is in no card
        text and Strength must be applied to them by hand."""
        blade = {"name": "Blade Dance", "cost": "1", "type": "Skill",
                 "can_play": True, "description": "Add 3 Shivs into your Hand.",
                 "target_type": "Self"}
        gs = _state()
        full = combat._effective_damage(blade, gs.enemies[0], 0, gs.hand, 0)
        weak = combat._effective_damage(blade, gs.enemies[0], 0, gs.hand, -2)
        assert weak < full


class TestDamageMultiplier:
    def test_double_damage_is_detected(self):
        assert combat._player_damage_multiplier(_state(DOUBLE_DAMAGE)) == 2.0

    def test_a_block_doubler_is_not_a_damage_multiplier(self):
        assert combat._player_damage_multiplier(_state(SHADOWMELD)) == 1.0

    def test_lethal_accounts_for_doubled_damage(self):
        """6 damage does not kill 10 HP, but doubled it does."""
        gs = _state(enemy_hp=10)
        assert not combat._kills_enemy(STRIKE, gs.enemies[0], 0, gs.hand, 0, 0, 1.0)
        assert combat._kills_enemy(STRIKE, gs.enemies[0], 0, gs.hand, 0, 0, 2.0)

    def test_the_kill_is_actually_taken(self):
        gs = _state(DOUBLE_DAMAGE, enemy_hp=10)
        action, fields = combat.decide(gs)
        played = next(c["name"] for c in gs.hand if c["index"] == fields["card_index"])
        assert played == "Strike"

    def test_no_false_lethal_when_the_hit_is_zeroed(self):
        """At Strength -6 the game reports the Strike as "Deal 0 damage"."""
        gs = _state(STRENGTH_DOWN, enemy_hp=5)
        zeroed = dict(STRIKE, description="Deal 0 damage.")
        assert not combat._kills_enemy(zeroed, gs.enemies[0], 0, gs.hand, 0, -6, 1.0)
