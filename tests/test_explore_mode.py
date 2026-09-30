"""Relic exploration mode.

The relic history is self-selecting: Golden Pearl has the most recorded runs
because "gain 150 Gold" is the one upside the scorer reads as a number, so it
wins ties. Relics the bot rarely picks stay at n=1 and can never be judged.
Explore mode takes the least-sampled relic on offer instead.
"""
import os
from collections import Counter

import pytest

from bot import explore


@pytest.fixture
def explore_on(monkeypatch):
    monkeypatch.setenv(explore.ENV_VAR, "1")
    yield
    monkeypatch.delenv(explore.ENV_VAR, raising=False)


def _opt(index, relic, locked=False):
    return {"index": index, "title": relic, "relic_name": relic, "is_locked": locked,
            "description": f"{relic} does something."}


def test_off_unless_the_env_var_says_otherwise(monkeypatch):
    monkeypatch.delenv(explore.ENV_VAR, raising=False)
    assert explore.enabled() is False
    monkeypatch.setenv(explore.ENV_VAR, "1")
    assert explore.enabled() is True
    monkeypatch.setenv(explore.ENV_VAR, "0")
    assert explore.enabled() is False


def test_picks_the_relic_with_the_fewest_recorded_runs():
    options = [_opt(0, "Golden Pearl"), _opt(1, "Lead Paperweight"), _opt(2, "Pomander")]
    counts = Counter({"Golden Pearl": 7, "Pomander": 3, "Lead Paperweight": 1})
    chosen = explore.pick_least_sampled(options, lambda o: 10.0, counts)
    assert chosen["relic_name"] == "Lead Paperweight"


def test_ties_break_on_the_normal_score():
    options = [_opt(0, "A"), _opt(1, "B")]
    counts = Counter()  # both unseen
    scores = {"A": 5.0, "B": 9.0}
    chosen = explore.pick_least_sampled(options, lambda o: scores[o["relic_name"]], counts)
    assert chosen["relic_name"] == "B"


def test_never_takes_a_locked_or_rejected_option():
    options = [_opt(0, "Locked One", locked=True), _opt(1, "Fine One")]
    chosen = explore.pick_least_sampled(options, lambda o: 10.0, Counter())
    assert chosen["relic_name"] == "Fine One"

    # Everything rejected by the normal scorer -> no explore pick at all.
    assert explore.pick_least_sampled(options, lambda o: -1.0, Counter()) is None


def test_no_relic_options_means_no_explore_pick():
    plain = [{"index": 0, "title": "Gain 50 Gold", "description": "Gain 50 Gold."}]
    assert explore.pick_least_sampled(plain, lambda o: 10.0, Counter()) is None
