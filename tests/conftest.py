import json
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# allow `from bot...` imports when running pytest from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """Reset the module-level caches between tests.

    `deck_memory` deliberately holds the deck across screens (the piles are
    only visible in combat), and the card_select/menu handlers track progress
    within one visit. In a single pytest process that state would otherwise
    leak from one test into the next -- which showed up as a card-reward test
    skipping because a previous test's deck was still in memory.
    """
    from bot import deck_memory, route_memory
    from bot.strategy import combat, menu, misc_screens, rewards

    deck_memory.reset()
    route_memory.reset()
    combat.reset_turn_memory()
    rewards._skipped_card_rewards.clear()
    misc_screens._card_select_visit["signature"] = None
    misc_screens._card_select_visit["picked"] = set()
    menu._silent_selected_this_visit = False
    yield
    deck_memory.reset()


@pytest.fixture(autouse=True)
def _isolate_relic_history(tmp_path, monkeypatch):
    """Keep the test suite out of the real relic history.

    `RunRecorder` calls `relic_stats.record_run` on every `game_over`, and
    those tests pass a `tmp_path` for the recorder's own logs but not for the
    stats file, which is a module-level constant. So every pytest run appended
    its fixtures to `stats/relic_history.jsonl` -- the file kept deliberately
    outside `logs/` so it is never wiped. 54 synthetic rows had accumulated
    there against 21 real runs, dragging the reported average floor from ~12
    down to 6.4.
    """
    from bot import relic_stats

    monkeypatch.setattr(relic_stats, "STATS_DIR", tmp_path)
    monkeypatch.setattr(relic_stats, "HISTORY_FILE", tmp_path / "relic_history.jsonl")


@pytest.fixture(autouse=True)
def _record_during_tests(monkeypatch, tmp_path):
    """Run the suite with recording on, whatever this copy is set to.

    Recording is off unless a `RECORD_RUNS` marker sits beside the package (see
    `bot/recording.py`), and the public copy of this project ships without one.
    Every test of a writer -- the decision log, `run_recorder`, `run_story` --
    would then quietly assert nothing there, which is the kind of green suite
    that proves the least. So the suite always turns it on, and the handful of
    tests that check the off switch set STS2_RECORD=0 for themselves.
    """
    monkeypatch.setenv("STS2_RECORD", "1")

    # ...but never into the real logs/ folder. Building a BotLoop opens a
    # decision log, so a test that did not redirect LOG_DIR left an empty
    # run_<timestamp>.jsonl in the developer's own logs/ on every pytest run --
    # which is also what put a trail of empty stubs through every archived set.
    from bot import loop as loop_mod

    monkeypatch.setattr(loop_mod, "LOG_DIR", tmp_path / "bot_logs")

    # And with it on, keep the deck cache out of the real logs/ folder:
    # `remember()` is called all over the suite, and every one of those calls
    # used to write logs/deck_memory.json in the developer's own checkout.
    from bot import deck_memory

    monkeypatch.setattr(deck_memory, "_cache_path", lambda: tmp_path / "deck_memory.json")


@pytest.fixture(autouse=True)
def _never_touch_the_real_game(monkeypatch):
    """Stop the test suite from killing and relaunching the actual game.

    `test_loop.py` drives `BotLoop.run()` from seven places and none of them
    stubbed `kill_and_relaunch`. Any of those whose fixture reports a run
    already in progress reaches `_abandon_preexisting_run`, which force-kills
    Slay the Spire 2 and asks Steam to relaunch it -- for real, on the
    developer's machine. Those same tests also monkeypatch `time.sleep` to a
    no-op, so the 3-second pause between kill and relaunch vanished and a
    single pytest run fired seven `steam://rungameid` requests inside three
    seconds. That is exactly the burst Steam's own log showed before each
    unexplained restart, including several that landed mid-run during a live
    evaluation set.

    Individual tests that want to observe the call still override this with
    their own stub; this only guarantees the real one never runs.
    """
    import os

    from bot import loop as loop_mod
    from bot import recovery

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "kill_and_relaunch reached the real implementation during a test -- "
            "this force-kills the running game. Stub it in the test."
        )

    monkeypatch.setattr(recovery, "kill_and_relaunch", lambda **kw: None)
    monkeypatch.setattr(loop_mod, "kill_and_relaunch", lambda **kw: None)
    # Belt and braces: nothing in a test should ever hand a URL to the shell.
    monkeypatch.setattr(os, "startfile", _refuse, raising=False)


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)
