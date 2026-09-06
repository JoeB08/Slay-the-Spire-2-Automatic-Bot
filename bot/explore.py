"""Relic exploration mode: deliberately sample under-tested relics.

Normally the bot takes the best-scoring option at a relic choice. That makes
the relic history self-selecting -- Golden Pearl has the most recorded runs of
any relic precisely because "gain 150 Gold" is the one upside the scorer can
read as a number, so it wins ties by default. Relics the bot rarely picks stay
at n=1 forever and can never be judged.

In explore mode the bot instead takes the **least-sampled** relic on offer,
breaking ties on the normal score. Coverage is still bounded by what the game
offers -- you cannot target a specific relic, only take it when it appears --
so this widens the sample rather than filling it.

**Explore runs are not performance runs.** Deliberately taking relics you
suspect are worse lowers the average floor, so these runs are tagged
(`neow_explore`) and must be excluded when judging a code change. They are
still perfectly good for comparing relics against each other.

Off by default. Enabled with the STS2_EXPLORE_RELICS environment variable,
which `scripts/bot_control.ps1 -Explore` sets.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Any, Optional

from . import relic_stats

ENV_VAR = "STS2_EXPLORE_RELICS"


def enabled() -> bool:
    """Read at call time, not import time, so tests can toggle it."""
    return os.environ.get(ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def relic_sample_counts() -> Counter:
    """How many recorded runs each relic has been *acquired first* in.

    Counts the Neow-equivalent choice rather than every relic held, since that
    is the decision this mode is trying to spread evenly. Ascension-0 only, to
    match every other comparison in the project.
    """
    counts: Counter = Counter()
    for entry in relic_stats.load_history():
        others = [r for r in entry.get("relics", []) if r != relic_stats.STARTING_RELIC]
        if others:
            counts[others[0]] += 1
    return counts


def pick_least_sampled(
    options: list[dict[str, Any]], score_of, counts: Optional[Counter] = None
) -> Optional[dict[str, Any]]:
    """The relic option with the fewest recorded runs, or None if none qualify.

    `score_of(option) -> float` is the normal scorer, used only to break ties
    and to skip options the ordinary rules would refuse outright (locked ones,
    or anything scoring as a rejection).
    """
    if counts is None:
        counts = relic_sample_counts()

    relic_options = [
        o for o in options
        if o.get("relic_name") and not o.get("is_locked") and score_of(o) > 0
    ]
    if not relic_options:
        return None

    return min(relic_options, key=lambda o: (counts.get(o["relic_name"], 0), -score_of(o)))
