# TODO — resume from 2026-08-31

Bot **STOPPED**. 449 tests green. **Project is now under git** — every change
since the regression is committed and bisectable.

    git log --oneline
    6ec1a1d  Fix the divination screen hanging runs
    f22a197  Revert _defence_already_covered (26% blast radius)
    a3df583  Snapshot: build that scored 13.9

---

## 1. Run a 20-run set — this is the whole point now

    powershell -ExecutionPolicy Bypass -File scripts/bot_control.ps1 -Action start

**Baseline to beat: 20.9 (+/-3.8), best 33, act 2 in 5/15, dmg/floor 5.72.**

Nothing else should be changed until this number exists. The last three
sessions all ended with unmeasured changes stacked on unmeasured changes,
which is why the regression took a whole set to spot and could not be
attributed.

Watch **damage per floor**, not just average floor — it moved first both times.

## 2. If it comes back near 20

The revert worked; carry on. Seven unmeasured changes are still in the build
(`Asleep`, `boss_intel`, upgrade-attacks-early, Sly-outlet sequencing,
unaffordable-card valuation, dead-Block duds, Strength/Dexterity double-count
removal) and can be considered settled.

## 3. If it comes back near 14

The culprit is still in there. Bisect with git rather than guessing:
`a3df583` is the bad build, and the changes are in separate commits from here
on.

---

## What was established today (do not re-litigate)

* **The Strength/Dexterity double-count removal is CORRECT.** Across 9000+
  logged hands the game demonstrably pre-applies both to card text:
  dex -4 -> "Gain 1 Block", dex -2 -> "Gain 3 Block", dex +1 -> "Gain 6 Block".
  Subtracting them again was a real bug. **Never revert this.**
* **`_defence_already_covered` was reverted** — it fired on 26.2% of turns,
  far beyond the one pattern it targeted. Kept in source, marked unused.
* **Ruled out by measurement, not intuition:** under-blocking (0 of 365
  turns), card abandonment (0), deck size (a confound -- dying early means
  fewer card offers), `Asleep` (fires 4 times in 2028 turns).

## The divination bug (fixed, worth understanding)

Crystal Sphere is an 11x11 reveal minigame. The handler sent
`crystal_sphere_proceed` unconditionally; the game rejects it while
`can_proceed` is false. The bot erred on that screen until the freeze
detector relaunched the game and **abandoned a floor-31 run**.

`KNOWLEDGE.md` had flagged `crystal_sphere` as "unverified, no live fixture
ever captured" -- the action name was a guess. Fixture now saved at
`tests/fixtures/crystal_sphere.json`.

The click action name is **still unverified**. The handler probes four
candidates and remembers whichever is accepted. **Check the next log for which
one worked** and hard-code it:

    grep -o 'crystal_sphere_[a-z_]*' logs/run_*.jsonl | sort | uniq -c

## Still open, needs the user

* **Gnarled Hammer enchant screen** — loop is fixed (bounded confirms, then
  escape) but why the confirm never takes is unknown. Needs one live
  experiment: select 2 instead of 3, or re-select the same index, and watch
  `can_confirm`.
* **Map routing + boss knowledge** — routing looks healthy but has never known
  the boss. `boss_intel` reads it now; routing could seek a shop when the deck
  needs poison for Lagavulin. Speculative.

## Standing traps (each has bitten)

* Heredocs eat backslash escapes -- write patch scripts to a file.
* Helpers get written and left unwired. Grep call sites. (Happened again today
  with `note_crystal_sphere_result`.)
* Importing `bot.loop` opens a decision log, so analysis scripts leave empty
  logs and "newest by mtime" lands on one.
* The logged `incoming` field is an unfiltered summary, not what `decide()`
  saw.
* The game pre-applies Strength/Dexterity to card text.
* **Deck size at death is a confound**, not a cause. It fooled me twice.
* **Reproduce a reported behaviour against current code before fixing it.**
  Two regressions came from changing code that was already correct.
