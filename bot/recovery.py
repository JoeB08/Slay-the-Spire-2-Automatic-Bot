"""Recovery from a genuinely frozen game.

There's no "flee combat" (or any abandon-in-place) action in this API, so a
hard freeze -- the game engine itself stops advancing, not just a slow
animation -- can't be unstuck by sending it more actions. The only real fix
is killing and relaunching the game; STS2, like the original, autosaves
mid-run, so the main menu's "continue" (already the top preference in
strategy/menu.py) picks the run back up rather than losing progress.

Detection: fingerprint the raw state on every poll. If it's byte-identical
for longer than STUCK_TIMEOUT_SECONDS of real wall-clock time, that's not a
transitioning-screen delay (those resolve in a second or two, as observed
throughout live testing) -- it's a stall.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Optional

PROCESS_NAME = "SlayTheSpire2.exe"
STEAM_APP_ID = "2868840"
STUCK_TIMEOUT_SECONDS = 30
RELAUNCH_WAIT_ATTEMPTS = 60
RELAUNCH_WAIT_DELAY = 3.0
MAX_CONSECUTIVE_RECOVERIES = 3


def _fingerprint(raw: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


class StuckDetector:
    def __init__(self, timeout: float = STUCK_TIMEOUT_SECONDS):
        self.timeout = timeout
        self._fingerprint: Optional[str] = None
        self._since: float = time.time()

    def observe(self, raw: dict[str, Any]) -> bool:
        """Feed the latest raw state. Returns True once it's been unchanged
        for longer than `timeout` seconds -- i.e. time to recover."""
        fp = _fingerprint(raw)
        now = time.time()
        if fp != self._fingerprint:
            self._fingerprint = fp
            self._since = now
            return False
        return (now - self._since) > self.timeout

    def reset(self) -> None:
        self._fingerprint = None
        self._since = time.time()


# Every relaunch is appended here, outside `logs/` so it survives the wipes
# between evaluation sets. Steam's own log showed bursts of seven
# `steam://rungameid` requests preceding each restart, which one call from
# here cannot explain -- so each call records who asked for it, and the
# absence of a line is as informative as its presence.
RELAUNCH_LOG = Path(__file__).parent.parent / "stats" / "relaunch_log.txt"


def _note_relaunch(reason: str) -> None:
    try:
        RELAUNCH_LOG.parent.mkdir(exist_ok=True)
        caller = " | ".join(
            frame.strip().replace(chr(10), " ") for frame in traceback.format_stack(limit=4)[:-1]
        )
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(RELAUNCH_LOG, "a", encoding="utf-8") as f:
            print(f"[{stamp}] pid={os.getpid()} reason={reason}", file=f)
            print(f"    from: {caller}", file=f)
    except OSError:
        pass  # diagnostics are never worth failing a run over


def kill_and_relaunch(reason: str = "unspecified") -> None:
    """Force-kills the game and relaunches it through Steam (not the exe
    directly, so Steam's own DRM/overlay/cloud-save handling stays intact)."""
    _note_relaunch(reason)
    subprocess.run(["taskkill", "/IM", PROCESS_NAME, "/F"], capture_output=True)
    time.sleep(3)
    os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
