"""Which Act 1 a run rolled.

Winning a run unlocked a second Act 1 with a different encounter pool, so
results from before and after are not directly comparable. They are kept in
one dataset and *tagged*, rather than split, so the sample keeps growing.

The two pools overlap heavily -- 10 enemies appear in both -- so classification
keys only on enemies seen in one and never the other. An earlier reading that
they shared nothing came from a 10-enemy sample and was wrong; the shared list
is here explicitly so that mistake is not repeated.

The Act 1 boss is *not* a discriminator: Ceremonial Beast bosses both.

Pools were extracted from recorded runs (`state_type` monster/elite, floors
<= 17), not from guesswork. Unknown names classify as "unknown" rather than
being forced into a bucket -- a third variant should show up as unknown, not
be silently mislabelled.
"""
from __future__ import annotations

from typing import Iterable

# Seen only in the original Act 1.
CLASSIC_ONLY = frozenset({
    "Assassin Raider", "Axe Raider", "Brute Raider", "Bygone Effigy",
    "Crossbow Raider", "Cubex Construct", "Eye with Teeth", "Fogmog",
    "Inklet", "Leaf Slime (M)", "Leaf Slime (S)", "Mawler",
    "Slithering Strangler", "Tracker Raider", "Twig Slime (S)",
})

# Seen only in the Act 1 unlocked by winning a run.
UNLOCKED_ONLY = frozenset({
    "Corpse Slug", "Fat Gremlin", "Gas Bomb", "Gremlin Merc", "Living Fog",
    "Seapunk", "Skulking Colony", "Sneaky Gremlin", "Terror Eel", "Toadpole",
})

# Present in both -- carries no signal, kept so the overlap stays documented.
SHARED = frozenset({
    "Byrdonis", "Flyconid", "Fuzzy Wurm Crawler", "Nibbit", "Phrog Parasite",
    "Shrinker Beetle", "Snapping Jaxfruit", "Twig Slime (M)", "Vine Shambler",
    "Wriggler",
})

CLASSIC = "act1_classic"
UNLOCKED = "act1_unlocked"
UNKNOWN = "act1_unknown"
MIXED = "act1_mixed"


def classify(enemy_names: Iterable[str]) -> str:
    """Tag a run by the Act 1 pool its encounters came from.

    Returns `act1_mixed` if markers from both appear, which should not happen
    within one run -- if it does, the pools need re-deriving rather than the
    result being trusted.
    """
    names = {n for n in enemy_names if n}
    classic = bool(names & CLASSIC_ONLY)
    unlocked = bool(names & UNLOCKED_ONLY)
    if classic and unlocked:
        return MIXED
    if classic:
        return CLASSIC
    if unlocked:
        return UNLOCKED
    return UNKNOWN
