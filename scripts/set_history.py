"""Write a comparison of every run set to `stats/SET_HISTORY.md`.

    python scripts/set_history.py

Scans `stats/archive/*/runs.jsonl` plus the live `logs/runs.jsonl`, and reports
average floor with a margin of error, so a set's number is never read as more
precise than it is.

The margin matters more than the average here. Depth varies enormously run to
run -- floors 7 to 33 on one build -- so a 20-run set carries roughly +/-3
floors at 95%, and a 9-run set closer to +/-4. Two sets whose intervals overlap
have not been shown to differ.
"""
from __future__ import annotations

import json
import math
import statistics as st
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "stats" / "SET_HISTORY.md"

# What each set was actually testing. Without this the table is just numbers:
# the enemy pool changed part-way through, and several sets predate fixes that
# make their deck sizes incomparable.
NOTES = {
    "set_20260828_11runs": "First recorded set. Pre-fix build.",
    "set_20260828_20runs": "The long-standing `act1_classic` baseline.",
    "partial_20260828_prescoring": "Aborted; kept for its logs only.",
    "partial_20260828_newact1_prelog": "First runs after a second Act 1 unlocked.",
    "partial_20260829_5runs": "Paused for a human shadow run.",
    "set_20260829_9runs_fixedbuild": "First set on the fixed build; first `act1_unlocked` baseline.",
    "set_20260829_20runs_intrinsic": (
        "Intrinsic card scoring. Depressed by a confirmed defect: 46% of card "
        "rewards were abandoned unopened (skip memory leaked across runs). "
        "Read 15.5 as a floor, not a fair measure. See its README."
    ),
    "set_20260830_20runs_batch2": (
        "First set on the fixed batch: card starvation, sequencing, unblockable "
        "HP loss, Ringing, clog outlets, relic archetypes, shop order. Card "
        "abandonment 46% -> 0%. Did NOT include the afternoon's Act 2 work."
    ),
    "logs (current)": "In progress. Adds Act 2 boss handling, X-cost, Strength, Back Attack.",
}


def _load(path: Path):
    if not path.exists():
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _summarise(name: str, rows: list[dict]) -> dict | None:
    floors = [r.get("floor_reached", 0) for r in rows]
    if not floors:
        return None
    n = len(floors)
    sd = st.stdev(floors) if n > 1 else 0.0
    moe = 1.96 * sd / math.sqrt(n) if n > 1 else float("nan")
    variants = Counter(r.get("act1_variant") or "untagged" for r in rows)
    return {
        "set": name,
        "n": n,
        "avg": sum(floors) / n,
        "moe": moe,
        "best": max(floors),
        "act2": sum(1 for r in rows if r.get("act_reached", 1) >= 2),
        "variants": ", ".join(f"{k.replace('act1_', '')} {v}" for k, v in variants.most_common()),
        "floors": sorted(floors),
    }


def main() -> None:
    sets = []
    for d in sorted((ROOT / "stats" / "archive").glob("*/")):
        s = _summarise(d.name, _load(d / "runs.jsonl"))
        if s:
            sets.append(s)
    live = _summarise("logs (current)", _load(ROOT / "logs" / "runs.jsonl"))
    if live:
        sets.append(live)

    lines = [
        "# Run sets — average depth over time",
        "",
        f"_Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/set_history.py`._",
        "",
        "| Set | Runs | Avg floor | 95% margin | Best | Act 2 | Act 1 variant |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for s in sets:
        moe = "—" if math.isnan(s["moe"]) else f"±{s['moe']:.1f}"
        lines.append(
            f"| {s['set']} | {s['n']} | **{s['avg']:.1f}** | {moe} | {s['best']} | "
            f"{s['act2']}/{s['n']} | {s['variants']} |"
        )

    lines += [
        "",
        "## How to read this",
        "",
        "**The margin is the point.** Run depth varies hugely — one build produced",
        "floors 7 through 33 — so a 20-run set is worth roughly ±3 floors and a",
        "9-run set nearer ±4. **Two sets whose intervals overlap have not been",
        "shown to differ**, however different their averages look.",
        "",
        "**Only same-variant sets compare.** Winning a run unlocked a second Act 1",
        "with a completely different enemy pool. Sets before that are",
        "`act1_classic`; later ones are a mix. `unknown` means the run met only",
        "enemies common to both, so the variant genuinely cannot be determined.",
        "",
        "**Deck sizes are not comparable across sets.** Earlier sets counted",
        "combat-generated tokens, so a 14-card deck could report as 23. Only sets",
        "from 2026-08-29 onward use the true count.",
        "",
        "## What each set was testing",
        "",
    ]
    for s in sets:
        note = NOTES.get(s["set"], "")
        lines.append(f"* **{s['set']}** — {note} Floors: {', '.join(str(f) for f in s['floors'])}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for s in sets:
        moe = "n/a" if math.isnan(s["moe"]) else f"+/-{s['moe']:.1f}"
        print(f"  {s['set']:34} n={s['n']:<3} avg {s['avg']:5.1f} {moe}")


if __name__ == "__main__":
    main()
