import time

from bot.recovery import StuckDetector


def test_not_stuck_while_state_keeps_changing():
    d = StuckDetector(timeout=0.05)
    assert d.observe({"a": 1}) is False
    assert d.observe({"a": 2}) is False
    assert d.observe({"a": 3}) is False


def test_detects_stuck_after_timeout_with_identical_state():
    d = StuckDetector(timeout=0.05)
    assert d.observe({"a": 1}) is False
    time.sleep(0.1)
    assert d.observe({"a": 1}) is True


def test_reset_clears_stuck_state():
    d = StuckDetector(timeout=0.05)
    d.observe({"a": 1})
    time.sleep(0.1)
    assert d.observe({"a": 1}) is True
    d.reset()
    assert d.observe({"a": 1}) is False
