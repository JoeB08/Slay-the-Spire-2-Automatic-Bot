"""Artifact eats debuffs; Expose strips it.

    Artifact  "Negates 2 debuffs."                        (seen on real enemies)
    Expose    "Remove all Artifact and Block from the
               enemy. Apply 2 Vulnerable. Exhaust."       (0 cost)

The bot had no notion of Artifact at all, so every Weak/Vulnerable/Poison
aimed at a shielded enemy was silently eaten. Expose has to be played before
the rest of the debuffs, not alongside them.
"""
from bot.strategy import combat

EXPOSE = {"name": "Expose", "cost": "0",
          "description": "Remove all Artifact and Block from the enemy. Apply 2 Vulnerable. Exhaust."}
POISON = {"name": "Deadly Poison", "cost": "1", "description": "Apply 5 Poison."}
STRIKE = {"name": "Strike", "cost": "1", "description": "Deal 6 damage."}


def _enemy(artifact=0):
    status = ([{"name": "Artifact", "amount": artifact, "description": "Negates 2 debuffs."}]
              if artifact else [])
    return {"entity_id": "E0", "name": "E", "hp": 40, "max_hp": 40, "block": 0,
            "status": status, "intents": []}


def test_artifact_is_read_from_enemy_status():
    assert combat._enemy_artifact(_enemy(2)) == 2
    assert combat._enemy_artifact(_enemy()) == 0


def test_a_debuff_into_artifact_is_recognised_as_wasted():
    assert combat._debuff_is_wasted(POISON, _enemy(2)) is True
    assert combat._debuff_is_wasted(POISON, _enemy()) is False


def test_expose_is_not_wasted_because_it_strips_the_shield_itself():
    assert combat._strips_artifact(EXPOSE) is True
    assert combat._debuff_is_wasted(EXPOSE, _enemy(2)) is False


def test_a_plain_attack_is_unaffected_by_artifact():
    assert combat._debuff_is_wasted(STRIKE, _enemy(2)) is False


def test_expose_is_ordered_before_other_debuffs():
    # PLAY_EARLY (2) sorts ahead of PLAY_NORMAL (1).
    assert combat._play_timing(EXPOSE) == combat.PLAY_EARLY
    assert combat._play_timing(EXPOSE) > combat._play_timing(POISON)
