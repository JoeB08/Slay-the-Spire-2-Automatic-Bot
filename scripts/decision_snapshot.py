"""Record or compare the bot's decision on every archived payload.

    python scripts/decision_snapshot.py record  [out.json]
    python scripts/decision_snapshot.py compare [out.json]

Why this exists
---------------
`decide()` is the core of the bot and the tests, thorough as they are, are
synthetic. Refactoring it safely needs proof that *real* inputs still produce
identical outputs, so this replays every raw payload ever logged through the
current code and records what it chose.

Record before a refactor, compare after: any behaviour change shows up as a
diff with the exact payload that produced it. A pure cleanup should report
zero differences.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import log_io  # noqa: E402
from bot.game_state import GameState  # noqa: E402
from bot.strategy import combat  # noqa: E402
from bot.strategy import misc_screens, potions, rewards, shop  # noqa: E402

# Outside the project: the baseline is a scratch file, and logs/ is wiped
# between evaluation sets. Pass a path to keep one somewhere deliberate.
DEFAULT_OUT = Path(tempfile.gettempdir()) / "sts2_decisions.json"

# Screens worth replaying, and the entry point each one uses.
HANDLERS = {
    "monster": lambda gs: combat.decide(gs),
    "elite": lambda gs: combat.decide(gs),
    "boss": lambda gs: combat.decide(gs),
    "card_select": lambda gs: misc_screens.decide_card_select(gs),
    "hand_select": lambda gs: misc_screens.decide_hand_select(gs),
    "treasure": lambda gs: misc_screens.decide_treasure(gs),
    "rewards": lambda gs: rewards.decide_rewards(gs),
    "card_reward": lambda gs: rewards.decide_card_reward(gs),
    "shop": lambda gs: shop.decide_shop(gs),
}


def _payloads():
    seen = 0
    # Compressed logs are included, ordered as if they were not: payloads are
    # numbered by position here, so a baseline recorded before a set was
    # compressed stays comparable index by index afterwards.
    files = log_io.log_glob(ROOT / "logs" / "run_*.jsonl")
    files += log_io.log_glob(ROOT / "stats" / "archive" / "**" / "run_*.jsonl",
                             recursive=True)
    for path in files:
        try:
            fh = log_io.open_log(path)
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"raw"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw = row.get("raw")
                if not raw:
                    continue
                seen += 1
                yield seen, raw


def _decide(raw):
    """Run the right handler, returning a comparable string."""
    state_type = raw.get("state_type")
    handler = HANDLERS.get(state_type)
    if handler is None:
        return None
    try:
        gs = GameState(raw)
    except Exception as exc:  # noqa: BLE001
        return f"STATE_ERROR:{type(exc).__name__}"
    # Module-level memories make some handlers order-dependent; clear the ones
    # that expose a reset so replays are deterministic.
    for mod, name in ((misc_screens, "reset_crystal_sphere"),):
        fn = getattr(mod, name, None)
        if fn:
            fn()
    try:
        action, fields = handler(gs)
    except Exception as exc:  # noqa: BLE001
        return f"RAISED:{type(exc).__name__}:{exc}"
    return json.dumps([action, fields], sort_keys=True)


def _collect(limit: int | None):
    out = {}
    for i, raw in _payloads():
        if limit and i > limit:
            break
        result = _decide(raw)
        if result is not None:
            out[str(i)] = result
    return out


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "record"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    limit = None

    decisions = _collect(limit)
    if mode == "record":
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(decisions), encoding="utf-8")
        print(f"recorded {len(decisions)} decisions -> {out}")
        return

    if not out.exists():
        print(f"No baseline at {out}. Run 'record' first.")
        return
    before = json.loads(out.read_text(encoding="utf-8"))
    keys = set(before) | set(decisions)
    diffs = [k for k in keys if before.get(k) != decisions.get(k)]
    print(f"compared {len(decisions)} decisions against {len(before)} recorded")
    if not diffs:
        print("  IDENTICAL - the refactor changed no behaviour.")
        return
    print(f"  {len(diffs)} DIFFERENCES:")
    for k in sorted(diffs, key=lambda x: int(x))[:20]:
        print(f"    payload {k}:")
        print(f"       before: {before.get(k)}")
        print(f"       after : {decisions.get(k)}")


if __name__ == "__main__":
    main()
