"""The supervisor must tell "the mod is dead" apart from "the game is slow".

On 2026-11-18 a game update (v0.107.1 -> v0.111.1) renamed types the STS2MCP
assembly referenced, and the mod stopped loading. From outside the game the
symptom is only that the HTTP API never answers -- identical to a slow launch.
The supervisor answered it the only way it knew: relaunch, ten times, then give
up with "BROKEN" and nothing in its log naming the cause. Two sessions were
spent rediscovering it by hand.

The game's own log says it plainly, so the supervisor now reads it. These tests
pin the two directions that matter, because only one of them is obvious:

  * a log that says `Loaded 0 mods` must stop the set immediately, and
  * a log that is missing, empty or simply hasn't got there yet must NOT,

since silence is absence of evidence. A detector that stopped a set whenever it
could not prove the mod was fine would ground the bot on every slow start.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_set.py"

BROKEN = (
    "[INFO] Found mod manifest file STS2_MCP.json\n"
    "[INFO] Loading assembly DLL STS2_MCP.dll\n"
    "[ERROR] Exception thrown while loading mod STS2_MCP: "
    "System.Reflection.ReflectionTypeLoadException: Unable to load one or more "
    "of the requested types.\n"
    "Could not load type "
    "'MegaCrit.Sts2.Core.Entities.Multiplayer.LobbyPlayer' from assembly 'sts2'.\n"
    "[INFO]  --- RUNNING MODDED! --- Loaded 0 mods (1 total)\n"
)

HEALTHY = (
    "[INFO] Loading assembly DLL STS2_MCP.dll\n"
    "[INFO] Finished mod initialization for 'STS2 MCP' (STS2_MCP).\n"
    "[INFO]  --- RUNNING MODDED! --- Loaded 1 mods (1 total)\n"
)


def _load(tmp_path, body=None):
    spec = importlib.util.spec_from_file_location("rs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if body is not None:
        log = tmp_path / "godot.log"
        log.write_text(body, encoding="utf-8")
        module.GAME_LOG = log
    else:
        module.GAME_LOG = tmp_path / "does_not_exist.log"
    return module


def test_zero_mods_loaded_is_reported(tmp_path):
    reason = _load(tmp_path, BROKEN).mod_load_failure()
    assert reason is not None
    assert "0 mods" in reason


def test_the_reason_names_the_actual_exception(tmp_path):
    """A bare "it did not load" would send the next session back to the log."""
    reason = _load(tmp_path, BROKEN).mod_load_failure()
    assert "LobbyPlayer" in reason or "ReflectionTypeLoadException" in reason


def test_healthy_log_does_not_stop_the_set(tmp_path):
    assert _load(tmp_path, HEALTHY).mod_load_failure() is None


def test_missing_log_is_not_treated_as_failure(tmp_path):
    """No log yet is the normal state seconds after launch."""
    assert _load(tmp_path, None).mod_load_failure() is None


def test_empty_log_is_not_treated_as_failure(tmp_path):
    assert _load(tmp_path, "").mod_load_failure() is None


def test_log_without_the_modded_line_is_not_a_failure(tmp_path):
    """The game logs for a while before it gets to mod loading."""
    body = "[INFO] FMOD Sound System: Successfully initialized\n"
    assert _load(tmp_path, body).mod_load_failure() is None


def test_latest_launch_wins(tmp_path):
    """One log spans many launches; a failure from an earlier one is stale.

    This is the case that would otherwise strand the bot: the mod is fixed and
    loading fine, but a failure from before the fix is still in the file.
    """
    assert _load(tmp_path, BROKEN + HEALTHY).mod_load_failure() is None


def test_a_later_failure_is_still_caught(tmp_path):
    """And the reverse: a good launch does not excuse a later bad one."""
    assert _load(tmp_path, HEALTHY + BROKEN).mod_load_failure() is not None


def test_only_the_tail_is_read(tmp_path):
    """Game logs reach tens of MB; the detector must not read them whole."""
    body = ("[INFO] padding\n" * 200000) + HEALTHY
    module = _load(tmp_path, body)
    assert module.GAME_LOG.stat().st_size > 2_000_000
    assert module.mod_load_failure() is None
