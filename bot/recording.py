"""Whether this copy of the bot keeps a record of what it did.

The bot normally writes a lot per run: one decision log (~3 MB), a summary line
in `runs.jsonl`, a readable story under `logs/stories/`, an entry in the relic
history, a deck cache, and a line in the relaunch log. All of it exists to
*measure* the bot -- sets of 20 runs compared with confidence intervals, and
`scripts/decision_snapshot.py` replaying archived payloads to prove a refactor
changed no behaviour. None of it is needed to simply watch the bot play, and a
fresh clone filling up with logs nobody asked for is rude.

So recording is a switch, and it is the only difference between the development
copy of this project and the public one:

* a file named `RECORD_RUNS` beside this package turns it on;
* `STS2_RECORD=1` or `STS2_RECORD=0` overrides that for one process.

The development copy commits the marker file; the public copy ships without it.
The *code* is identical in both, which is the point -- there is no second
version of the bot to keep in sync, and no behaviour that only one of them has.

What the switch does not touch: the decisions themselves. Nothing in `strategy/`
or `loop.py`'s dispatch reads it, so the bot plays exactly the same game either
way. It only decides whether what happened gets written down.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "STS2_RECORD"
MARKER = "RECORD_RUNS"

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off")


def marker_path() -> Path:
    return Path(__file__).parent.parent / MARKER


def enabled() -> bool:
    """Read at call time, not import time, so tests and scripts can toggle it."""
    flag = os.environ.get(ENV_VAR, "").strip().lower()
    if flag in _ON:
        return True
    if flag in _OFF:
        return False
    return marker_path().exists()


def why() -> str:
    """One line for the startup banner, so it is obvious which mode you are in."""
    if enabled():
        where = "STS2_RECORD" if os.environ.get(ENV_VAR, "").strip() else MARKER
        return f"recording (enabled by {where})"
    return f"not recording -- no {MARKER} file, and STS2_RECORD is not set"
