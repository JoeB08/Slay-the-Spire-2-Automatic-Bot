# Current work — read this first if context was lost

Session state: **2026-08-30, end of day.** Bot **STOPPED**, logs cleared,
449 tests green, code in a verified state.

> **`REPORT_20260830.md`** is the full session report. `CHANGES.md` is the
> prioritised list, `KNOWLEDGE.md` durable game/API facts.

## Pick up here: one diagnosed bug with the fix already written

**Over-blocking traced to the value step.** Live turn:

    blk4   e5  inc7   Neutralize
    blk9   e4  inc5   Footwork+
    blk9   e3  inc5   Leg Sweep+     (18 block)
    blk9   e1  inc5   Backflip+      (11 block)
    blk37  e0  inc5   end_turn       37 BLOCK vs 5 INCOMING

Three Strikes held all turn; a 172 HP Vantom took 1 damage.

It is **not** the mitigation step -- that correctly declined
(`_mitigation_value` of Leg Sweep+ vs a 5-damage attack is 1, under
`MIN_MITIGATION_VALUE` of 3). It is the **value step**: `_is_pure_block` only
catches cards that do literally *nothing* else, so Leg Sweep+ ("Apply 3 Weak.
Gain 18 Block") passes and is played even when Block already covers the hit.

The fix is written and NOT applied: **`scripts/pending_fix_covered_block.py`**.
It adds `_defence_already_covered` -- skip a Block card in the value step when
the incoming hit is already fully blocked AND the card's mitigation is below
`MIN_MITIGATION_VALUE`. Run it, then `pytest`, then verify the turn above.

It was left unapplied deliberately: applying an unverified change as the last
act of a session is how the previous two regressions happened.

## What was reverted today, and the lesson

Two changes were undone after the 9-run set showed damage **per floor** going
the wrong way (5.72 -> 6.57) and sub-floor-13 deaths going 0% -> 22%:

* **The Leg Sweep dud rule** -- the original code already handled the reported
  case. The old rule only fired when *nothing* was attacking, so it could
  never have caused "2 Defends instead of Leg Sweep". **I fixed a non-bug and
  caused a regression.**
* **`_is_defensive_only`** -- kept in source, marked unused, reasoning intact.

**Lesson worth keeping:** before fixing a reported behaviour, reproduce it
against the *current* code first. Two of today's regressions came from
changing code that was already correct.

## Set history

| Set | n | Avg | Best | Act 2 | dmg/floor |
|---|---:|---:|---:|---:|---:|
| `set_20260829_20runs_intrinsic` | 20 | 15.4 | 33 | 2/20 | — |
| `set_20260830_20runs_batch2` | 20 | 19.9 | 43 | 6/20 | 5.98 |
| `set_20260830_15runs_act2work` | 15 | 20.9 | 33 | 5/15 | 5.72 |
| `partial_20260830_9runs_overcorrected` | 9 | 16.2 | 33 | 1/9 | 6.57 |

The jump from 15.4 to ~20 is real and attributable to the card-starvation fix
(46% of card rewards were abandoned unopened; now 0%). **Nothing since has
been shown to move the number**, and the last change set moved it backwards.

## Never measured

Live in the code, never run: `Asleep` handling, the Lagavulin poison/block
plan (`boss_intel`), the upgrade-attacks-early preference, Sly-outlet
sequencing, unaffordable-card valuation, dead-Block duds, and the
Strength/Dexterity double-count removal (below).

## The most consequential fix of the day

**The bot was double-counting Strength and Dexterity.** The game bakes both
into the card text before sending it -- at Dexterity -2 a Defend reads "Gain 3
Block", at Strength -2 a Strike reads "Deal 4 damage" -- and the bot subtracted
them again. The Dexterity half was long-standing; the Strength half was
introduced the same day by a "fix" of mine. Shivs are the one real exception:
they are generated, not printed, so Strength must be applied to them by hand.

## Two open questions for the user

1. **The Gnarled Hammer enchant screen** still cannot be satisfied. The loop
   is fixed (bounded confirms, then escape) but *why* the confirm does not
   take is unknown. Needs one live experiment: select only 2, or re-select the
   same index, and watch `can_confirm`.
2. **Map routing** looked healthy on inspection (2 elites/run, shops reached,
   6-deep lookahead) -- but it has never known the boss. Now that
   `boss_intel` reads it, routing *could* seek shops when it needs poison for
   Lagavulin. Speculative; not built.

## Tools

* `python scripts/audit_decisions.py` — scans a log for known defect classes.
  **Run it after every batch**: it caught both of today's regressions.
* `python scripts/update_report.py` — refreshes the report's live section.
* `python scripts/card_rewards_audit.py` — should report zero abandoned.
* `python scripts/set_history.py`, `scripts/relic_table.py`.

## Standing traps (each has bitten)

* Heredocs eat backslash escapes -- write patch scripts to a file instead.
* Helpers get written and left unwired. Grep call sites.
* Importing `bot.loop` opens a decision log as a side effect, so analysis
  scripts leave empty logs and "newest by mtime" lands on one.
* The logged `incoming` field is an unfiltered summary, not what `decide()`
  reasoned with.
* **The game pre-applies Strength/Dexterity to card text.** Never apply them
  again.
