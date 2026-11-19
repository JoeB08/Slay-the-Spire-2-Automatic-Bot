"""The supervisor must tell "nothing is happening" apart from "the bot is busy".

Two failures on 2026-11-19, both of which it watched without noticing.

**A log that grows is not a bot that works.** The game wedged at 04:06. The
API answered nothing for 40 minutes. The bot wrote a `get_state_error` record
every 11 seconds, so the decision log grew the whole time, so `static_for`
never rose above zero, so neither stall branch could fire -- and the heartbeat
printed the fixed string "bot alive, API up" at 04:15, 04:30 and 04:45.

**A dead end is not a crash.** After the eleventh run the game disabled every
main-menu button except Settings and Quit, pending a manual unlock reveal that
STS2MCP refuses to perform from automation. No relaunch can clear that, so the
supervisor has to stop and say so rather than spend MAX_RESTARTS proving it.

The tests pin both directions in each case, because only one is obvious: the
detectors must fire on the real thing, and must NOT fire on a bot that is
merely slow, starting up, or mid-relaunch. A detector that stopped the set
whenever it could not prove health would ground the bot every night.
"""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_set.py"


def _load():
    spec = importlib.util.spec_from_file_location("rs_stall", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(tmp_path, records):
    path = tmp_path / "run_test.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


ERROR = {"event": "get_state_error",
         "error": "HTTPConnectionPool(host='localhost', port=15526): Read timed out."}
PLAY = {"state": {"state_type": "combat", "floor": 12}, "action": "play_card",
        "fields": {"card_index": 2}, "result": "ok"}


# --- the log-growth hole -------------------------------------------------

def test_a_log_of_nothing_but_failed_reads_is_a_stall(tmp_path):
    module = _load()
    assert module.only_failed_reads(_log(tmp_path, [ERROR] * 50)) is True


def test_a_bot_that_is_playing_is_not_a_stall(tmp_path):
    module = _load()
    assert module.only_failed_reads(_log(tmp_path, [PLAY] * 50)) is False


def test_one_real_decision_among_the_errors_is_not_a_stall(tmp_path):
    """A bot recovering by itself writes errors AND progress. Leave it alone."""
    module = _load()
    records = [ERROR] * 30 + [PLAY] + [ERROR] * 5
    assert module.only_failed_reads(_log(tmp_path, records)) is False


def test_old_errors_do_not_condemn_a_bot_that_has_recovered(tmp_path):
    """Only the tail counts: 500 old errors then real play is a healthy bot."""
    module = _load()
    records = [ERROR] * 500 + [PLAY] * 40
    assert module.only_failed_reads(_log(tmp_path, records)) is False


def test_an_empty_or_missing_log_is_not_a_stall(tmp_path):
    module = _load()
    assert module.only_failed_reads(None) is False
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert module.only_failed_reads(empty) is False


def test_an_unparseable_tail_is_not_a_stall(tmp_path):
    """Restarting resets the bot's own escalation, so guess in its favour."""
    module = _load()
    path = tmp_path / "half.jsonl"
    path.write_text('{"event": "get_state_error"}\n{"event": "get_st',
                    encoding="utf-8")
    assert module.only_failed_reads(path) is False


def test_only_the_tail_of_a_huge_log_is_read(tmp_path):
    """Decision logs reach 45 MB in a night; this runs every 60 seconds."""
    module = _load()
    path = _log(tmp_path, [PLAY] * 60000)
    assert path.stat().st_size > 5_000_000
    assert module.only_failed_reads(path) is False


def test_the_hard_threshold_is_past_a_steam_relaunch(tmp_path):
    """The bot relaunches the game itself and Steam takes about two minutes.

    This branch fires without the log-growth corroboration, so its threshold
    has to clear a legitimate relaunch on its own or it will restart a bot
    that was busy doing the right thing.
    """
    module = _load()
    assert module.API_DOWN_HARD_POLLS > module.API_DOWN_POLLS
    assert module.API_DOWN_HARD_POLLS * module.POLL_SECONDS >= 300


# --- the menu dead end ---------------------------------------------------

def _serve(module, state):
    """Point menu_dead_end() at a fixed state instead of the real game."""
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(state).encode("utf-8")

    module.urllib.request.urlopen = lambda *a, **k: _Resp()


BLOCKED = {
    "state_type": "menu", "menu_screen": "main", "message": "Main menu.",
    "options": ["settings", "quit"],
    "blocked_options": [{"name": "timeline", "enabled": False,
                         "reason": "manual_epoch_reveal_required",
                         "pending_epoch_ids": ["CUSTOM_AND_SEEDS_EPOCH"]}],
}


def test_the_real_dead_end_is_caught():
    module = _load()
    _serve(module, BLOCKED)
    why = module.menu_dead_end()
    assert why is not None
    assert "settings" in why


def test_the_reason_names_the_pending_unlock():
    """Without the epoch id the next session has to rediscover the cause."""
    module = _load()
    _serve(module, BLOCKED)
    assert "CUSTOM_AND_SEEDS_EPOCH" in module.menu_dead_end()


def test_a_menu_offering_singleplayer_is_fine():
    module = _load()
    _serve(module, {"state_type": "menu", "menu_screen": "main",
                    "options": ["singleplayer", "compendium", "settings", "quit"]})
    assert module.menu_dead_end() is None


def test_a_menu_offering_continue_is_fine():
    module = _load()
    _serve(module, {"state_type": "menu", "menu_screen": "main",
                    "options": ["continue", "abandon_run", "settings", "quit"]})
    assert module.menu_dead_end() is None


def test_a_still_loading_menu_is_not_a_dead_end():
    """At 6.6s after launch the API reports the main menu with options None.

    Firing here would kill every set two seconds after the game opened.
    """
    module = _load()
    for options in (None, []):
        _serve(module, {"state_type": "menu", "menu_screen": "main",
                        "options": options})
        assert module.menu_dead_end() is None


def test_a_run_in_progress_is_not_a_dead_end():
    module = _load()
    _serve(module, {"state_type": "combat", "floor": 14, "hp": 42})
    assert module.menu_dead_end() is None


def test_another_menu_screen_is_not_judged():
    """Character select legitimately offers neither continue nor singleplayer."""
    module = _load()
    _serve(module, {"state_type": "menu", "menu_screen": "character_select",
                    "options": ["SILENT", "confirm"]})
    assert module.menu_dead_end() is None


def test_dict_shaped_options_are_read_too():
    """Menu options come back as bare strings on some screens, dicts on others."""
    module = _load()
    _serve(module, {"state_type": "menu", "menu_screen": "main",
                    "options": [{"name": "singleplayer", "enabled": True}]})
    assert module.menu_dead_end() is None


def test_an_unreachable_api_is_not_a_dead_end():
    """That is the API-down branch's business, and it has its own patience."""
    module = _load()

    def boom(*a, **k):
        raise OSError("refused")

    module.urllib.request.urlopen = boom
    assert module.menu_dead_end() is None


def test_it_takes_two_polls_to_act():
    """One poll is a glimpse; the supervisor shuts the night down over this."""
    module = _load()
    assert module.DEAD_END_POLLS >= 2
