"""Write a readable Act 1 relic report to `stats/RELIC_TABLE.md`.

Regenerate after any run set:

    python scripts/relic_table.py

Reads `stats/relic_history.jsonl`, which lives outside `logs/` and is never
wiped -- relic signal only emerges as runs accumulate across many sets.

Two things this deliberately separates:

* **The first relic acquired** is the Neow choice: one decision made at a fixed
  point in every run, so it is comparable across runs.
* **Every Act 1 relic held** includes relics picked up at floor 15, which look
  good merely for having been acquired late by a run that was already going
  well. Read that table with the run counts, not the averages.

Ascension-0 runs only, and relic-exploration runs are excluded from averages:
those deliberately take under-sampled relics, so they drag the mean down for
reasons that have nothing to do with the relic.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import relic_stats  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "stats" / "RELIC_TABLE.md"
STARTER = relic_stats.STARTING_RELIC
MIN_FOR_SIGNAL = 3  # below this, treat a row as an anecdote


def _rows(entries, pick):
    """pick(entry) -> iterable of relic names to credit for that run."""
    floors: dict[str, list[int]] = defaultdict(list)
    for e in entries:
        for name in pick(e):
            if name and name != STARTER:
                floors[name].append(e.get("floor_reached", 0))
    out = [
        {"relic": r, "runs": len(f), "avg": sum(f) / len(f), "best": max(f), "floors": sorted(f)}
        for r, f in floors.items()
    ]
    out.sort(key=lambda r: (-r["avg"], -r["runs"]))
    return out


def _table(rows, note_singletons=True):
    lines = ["| Relic | Runs | Avg floor | Best | Floors reached |",
             "|---|---:|---:|---:|---|"]
    shown = [r for r in rows if r["runs"] >= MIN_FOR_SIGNAL]
    for r in shown:
        lines.append(f"| {r['relic']} | {r['runs']} | **{r['avg']:.1f}** | {r['best']} | "
                     f"{', '.join(str(f) for f in r['floors'])} |")
    if not shown:
        lines.append("| _(nothing has reached 3 runs yet)_ | | | | |")
    thin = [r for r in rows if r["runs"] < MIN_FOR_SIGNAL]
    if thin and note_singletons:
        lines.append("")
        lines.append(f"<details><summary>{len(thin)} relics with fewer than "
                     f"{MIN_FOR_SIGNAL} runs — too thin to read</summary>")
        lines.append("")
        lines.append("| Relic | Runs | Avg floor | Floors |")
        lines.append("|---|---:|---:|---|")
        for r in sorted(thin, key=lambda r: -r["avg"]):
            lines.append(f"| {r['relic']} | {r['runs']} | {r['avg']:.1f} | "
                         f"{', '.join(str(f) for f in r['floors'])} |")
        lines.append("")
        lines.append("</details>")
    return "\n".join(lines)


def main() -> None:
    history = relic_stats.load_history()          # ascension 0 by default
    entries = [e for e in history
               if e.get("relics") and e["relics"] != [STARTER] and not e.get("explore")]
    if not entries:
        print(f"No usable runs in {relic_stats.HISTORY_FILE}.")
        return

    floors = [e.get("floor_reached", 0) for e in entries]
    tagged = [e for e in entries if e.get("relic_act")]

    def first_relic(e):
        others = [r for r in e.get("relics", []) if r != STARTER]
        return others[:1]

    def act1_relics(e):
        return [n for n, a in (e.get("relic_act") or {}).items() if int(a) == 1]

    body = f"""# Act 1 relics vs how far the run got

_Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/relic_table.py` — rerun it
after any set to refresh._

**{len(entries)} runs** at Ascension 0 · average floor **{sum(floors)/len(floors):.1f}** ·
best **{max(floors)}** · {len(tagged)} runs carry per-act relic tags.

> Read the run counts before the averages. Depth varies enormously run to run
> (floors 7 to 33 on the same build), so a relic with 3 runs tells you very
> little. Rows below {MIN_FOR_SIGNAL} runs are folded away for that reason.

## The Neow choice — first relic acquired

This is the one relic decision made at a fixed point in every run, so it is the
only genuinely comparable one.

{_table(_rows(entries, first_relic))}

## Every relic acquired during Act 1

Includes relics picked up at floor 15, which look good partly for having been
acquired by a run that was already going well. Directional only.

{_table(_rows(tagged, act1_relics))}

## Caveats worth keeping in mind

* These measure **how far this bot gets** with a relic, not how good the relic
  is. A relic rewarding play the bot does not yet do will look weak.
* The Act 1 enemy pool changed part-way through data collection (a second Act 1
  unlocked), so runs before and after faced different enemies. Runs are tagged
  `act1_classic` / `act1_unlocked` in `logs/runs.jsonl` but relic history
  predates that split.
* Relic-exploration runs are excluded here — they deliberately take
  under-sampled relics and would drag averages down for the wrong reason.
"""
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  {len(entries)} runs, {len(tagged)} with per-act tags")


if __name__ == "__main__":
    main()
