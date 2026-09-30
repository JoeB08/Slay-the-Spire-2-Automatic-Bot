"""Tagging which Act 1 a run rolled.

Winning a run unlocked a second Act 1. Runs from before and after stay in one
dataset and are tagged rather than split, so the sample keeps growing -- but
they must never be pooled blindly in a comparison.
"""
from bot import act_variant as av


def test_classifies_on_exclusive_markers():
    assert av.classify(["Wriggler", "Mawler"]) == av.CLASSIC
    assert av.classify(["Wriggler", "Terror Eel"]) == av.UNLOCKED


def test_shared_enemies_carry_no_signal():
    """The two pools overlap by 10 enemies. An earlier reading that they
    shared nothing came from a 10-enemy sample and was wrong -- a run seen
    only through shared enemies is genuinely unknown, not classic."""
    assert av.SHARED & av.CLASSIC_ONLY == frozenset()
    assert av.SHARED & av.UNLOCKED_ONLY == frozenset()
    assert av.classify(sorted(av.SHARED)) == av.UNKNOWN
    assert av.classify([]) == av.UNKNOWN


def test_markers_from_both_pools_are_flagged_not_guessed():
    assert av.classify(["Mawler", "Terror Eel"]) == av.MIXED


def test_the_act_1_boss_is_not_used_as_a_discriminator():
    # Ceremonial Beast bosses both Act 1s, so it must not appear in either
    # exclusive set.
    assert "Ceremonial Beast" not in av.CLASSIC_ONLY
    assert "Ceremonial Beast" not in av.UNLOCKED_ONLY
