# Changes — needed, in progress, and done

Living document. **Update it in the same edit as the code change**, not
afterwards. Every entry says what the evidence was, because the recurring
failure mode on this project has been acting on a single observation and
over-correcting.

Companion file: `KNOWLEDGE.md` -- how the game and API actually behave.

Two orthogonal labels are used, and they are **not** the same thing:

**Evidence grade** — how much the claim can be trusted:

| Grade | Meaning |
|---|---|
| **A** | A defect provable from logs or a test. True regardless of run outcomes. |
| **B** | A pattern across 20+ recorded runs. |
| **C** | From a single run (bot or human). A hypothesis, not a finding. |

Nothing at grade C gets acted on alone. The human floor-48 run is one run and
could have been luck.

**Priority** — the numbered order of the "To do" section below. Grade and
priority diverge on purpose: extending the analyzer is grade A but priority 4,
because it measures rather than improves; the elite wall is grade B but
priority 2, because it is where runs actually end.

---

## Done — housekeeping (2026-09-30, no behaviour change)

None of this touches a decision. The bot plays the same game before and after,
which is the claim each item had to prove rather than assert.

* **Archived decision logs are gzipped — 396 MB to 13 MB.** The whole project
  went from 402 MB to 19 MB. The archives cannot simply be deleted: they are the
  only real regression test here, since `decision_snapshot.py` replays every
  payload in them to show a refactor changed nothing. They were also JSON text
  repeating the same field names 90,000 times.

  `scripts/compress_logs.py` packs each log to a temporary name, reads it back,
  and compares SHA-256 with the original before deleting it. `runs.jsonl`, all
  markdown, `bot_stdout.log` and zero-byte stub logs are left plain, the last of
  those so the `st_size > 0` filters that skip them keep working.

  Every reader now goes through `bot/log_io.py`. The subtle requirement was
  **ordering**: `decision_snapshot.py` and `analysis/replay_compare.py` key
  payloads by position and compare index by index, so `log_glob` sorts on the
  name with the suffix stripped. Without that, a baseline recorded before
  packing would compare different payloads afterwards and report differences
  that were not there.

  *Evidence it changed nothing:* the same 116 logs still yield 90,219 rows
  (65,805 with a `raw` payload) in the same order, with an identical SHA-256
  over the whole concatenated stream; and nine report scripts —
  `analyze_runs`, `set_history`, `card_rewards_audit`, `audit_decisions`,
  `archetype_report`, `analysis/killers`, `analysis/fight_cost`,
  `analysis/boss_by_hp`, `analysis/dying_kill_scan` — produce byte-identical
  output to their pre-compression runs. `tests/test_log_io.py` covers the
  round trip, the BOM, a truncated final line, and the ordering.

* **Recording is opt-in (`bot/recording.py`).** Six things used to be written
  unconditionally: the decision log, `runs.jsonl`, the run story, the relic
  history, the deck cache and the relaunch log. They are now gated on a
  `RECORD_RUNS` file beside the package, overridable with `STS2_RECORD=1`/`0`.
  This copy commits the marker, so nothing here changes.

  Nothing in `strategy/` or the pipeline reads the switch, so the decisions are
  untouched; with recording off the deck is still remembered in memory and a
  finished run still resets its bookkeeping, both covered by
  `tests/test_recording.py` along with the positive control.

* **A public copy, generated rather than forked** — `scripts/make_public_copy.py`
  builds `C:\sts2-silent-bot` from `git ls-files`, holding back `logs/`,
  `stats/`, the session reports, the working notes, `.claude/` and
  `scripts/analysis/` (hardcoded to this machine). The only difference between
  the two copies is the **absence of a file**, so there is no second bot to keep
  in sync and no question of which copy a bug lives in. The script refuses a
  destination it did not create, scans the result for anything naming this
  machine, and asks the exported copy itself whether recording is off.

  *Verified:* the copy's own suite passes (558 tests), and driving its `BotLoop`
  through three turns of a stubbed fight created no files at all.

* Two small fixes found on the way: `decision_snapshot.py`'s default baseline
  path was a personal scratch directory (now the OS temp dir), and the test
  suite wrote `logs/deck_memory.json` into the real `logs/` folder on any test
  that called `deck_memory.remember` (now `tmp_path`).

---

## Done and measured

Everything here is in `master` and has been through a set. The full write-up for
each batch -- the payloads, the reasoning, the per-run evidence -- is in the
report of the set that measured it, archived beside that set's data. The prose
that used to sit in this section was moved out on 2026-10-08; git has it.

| Batch | Measured by | Verdict |
|---|---|---|
| **2026-08-29 (late)**, 11 changes | `set_20260830_20runs_batch2` — 19.9 (+/-3.7), or 19.2 over 19 excluding one contaminated run | the lift off the 13.6-average era |
| **2026-09-10**, 13 changes | set 3, `set_20260911_20runs_merged` — 19.2 (+/-3.9) | **no measurable change**; every number inside set 2's margin |
| **2026-09-11**, 8 changes from set 3's findings, one fix after the merge, and the 6 found during set 4's first attempt | set 4, `partial_20260912_16runs_set4` — 23.2 (+/-4.4) over 16 runs | **no proven change**; nominally ahead on all three measures, every interval overlapping |

> **Corrected 2026-10-08.** This section used to head the last group "staged
> during set 4 — on `next-set-fixes`, **not in its build**". That was written
> during set 4's first attempt, which was then abandoned and restarted on the
> user's call so that every counted run carried those fixes. All five are in
> `06816a4`, set 4's own build: they were committed at 09-11 11:06 against a set
> that started at 21:31. Set 4 measured them.

### What was in each batch

**2026-08-29 (late)** — the skip-memory leak (the largest defect found on this
project); "lose N HP" is unblockable; held Status cards get played away; capped
turns (Ringing); hand clog; relics feed archetype detection; one engine card is
a direction, not a build; potion magnitude; shop order is relic → removal →
cards; discard tier 4 agrees with the purge step; sequencing consolidated.

**2026-09-10** — the loop ended turns with playable cards; card potions were
only drunk when dying; Sly Block cards kept for a free discard; Fan of Knives
first against a crowd; a hand-discard whose discard is the payoff is not a dud;
the Waterfall Giant's last turn spent on Block; random-target damage is not a
promised kill; two incoming-damage calculations disagreed (the widest of these);
most Block for the energy; Normality caps plays, not just energy; leaders first
with minions not ignored; the Gambit; and the Lagavulin poison-on-offer rule
removed at the user's request.

**2026-09-11** — no kill that leaves a lethal hit coming; HP carried along the
route with a heal before the boss or a long stretch; Giant potions wait for its
death blow; Skittish counted; Smoggy and Gas Bombs; Sly draw/energy cards not
paid for; draw first on a dying turn; Accuracy in card rewards. Plus, from set
4's first attempt: a dangerous route is not a dead one; attack on the Lagavulin
Matriarch's last asleep turn; the relaunch loop (a successful menu click reset
the recovery counter, so the escape never fired); resume a paused run
(`STS2_KEEP_RUN`); the whole map in the log; Decimillipede segments revive.

---

## Done — not yet measured

Set 5 (2026-10-08 to 2026-11-13, 20 runs) was a replication of set 4 at full
size on a `bot/strategy/` byte-identical to set 4's: **24.1 (+/-5.0)** average
floor against set 4's 23.2 (+/-4.4), 16/20 reaching the act 1 boss, 6.3 damage
per floor. Overlapping margins, as expected of a replication — no difference
demonstrated, and none was being tested.

Staged on `next-set-fixes`, from the 11-13 stall (below): **four recovery-path
fixes, no change to how a game is played.** Two of them are in
`bot/strategy/`, which normally would end a set's comparability, but neither
can alter a decision the game currently accepts — one only fires after four
rejected actions on one screen, the other is a new public alias. So a set on
this build remains comparable with set 5 for gameplay; it is the stall that
should disappear, not the numbers that should move.

| Fix | Evidence | Where |
|---|---|---|
| The mod's rejection message is reported, not dropped | **A** — every Crystal Sphere rejection logged `-> None` for want of reading any key but `message`; the reply that would name the right action was discarded | `api_client._error_detail` |
| An exhausted action probe stays exhausted | **A** — the give-up path called `reset_crystal_sphere()`, which restarted the candidate list on the next poll: the anti-loop path *was* the loop | `misc_screens.decide_crystal_sphere` |
| A screen that rejects everything ends the run | **A** — 5 rejected actions in a row on one screen means an unsatisfiable prompt, not a frozen engine, so resuming the autosave lands straight back on it. Relaunch once and abandon instead of spending the freeze grace | `loop._recover_from_stuck`, `ERROR_STREAK_UNPLAYABLE` |
| A save waiting on the main menu is abandoned, not resumed | **A** — a fresh process sees a plain menu, so the in-progress check never fired and the menu default resumed the stuck run. 52 restarts, 52 resumes. `STS2_KEEP_RUN` still wins | `loop._abandon_preexisting_run` |

14 tests cover them (`tests/test_crystal_sphere_stall.py`, and two added to
`tests/test_startup_abandon.py`); 572 pass.

### The two measurement tools, 2026-11-13 (recommendations 1 and 4)

Neither touches a decision; `bot/` cannot import from `scripts/`.

| Tool | What it answers | Evidence |
|---|---|---|
| `scripts/damage_per_round.py` | Damage dealt per round in the fight that ended each run, grouped by how far the run got | **A** — reproduces the figures recommendation 1 derived by hand: set 4's boss losses at 9.7 mean / 8.7 median / 7.5-13.0 range, every run's fight matching its recorded killer |
| `scripts/recover_interrupted.py` | Runs that never reached `game_over`, rebuilt from the decision log into `runs_interrupted.jsonl` | **A** — recovered both known losses: set 4's seventeenth run and the act 3 floor 39 run of 11-13 (1802 decisions across 56 relaunch fragments) |

Two things the first tool says that recommendation 1 did not:

* The groups **overlap** once every run is included. Runs that reached act 2
  go down to 12.4 (a floor-31 death to Entomancer) against a boss loss as high
  as 13.0 (Vantom). What separates cleanly is narrower and more useful: the
  seven act 1 boss losses cluster at 9.7 (+/-1.6), and the one fight that beat
  that boss sits at 14.8. The tool prints `OVERLAPPING` when this happens
  rather than asserting a separation it cannot show.
* The deciding fight of a deep run is act 2 content, not the act 1 boss, so
  the two groups are not measuring the same thing. Judging an act-1 deck
  change wants the act-1 boss fight specifically.

The second tool immediately changed a conclusion, which is the argument for
having built it: set 4's seventeenth run **won** its act 1 boss fight at 14.8
damage per round and reached floor 31, where an interruption ended it. Every
document here described that fight as nearly winning. The bar for beating the
act 1 boss is therefore demonstrated rather than estimated, and set 4's real
record is 17 runs with 8 of 15 boss attempts won.

Reconstructed rows go in `runs_interrupted.jsonl`, never in `runs.jsonl`:
appending them would silently change the figures of sets whose reports are
already written, and a set's numbers must not move under whoever reads them
next. `damage_per_round.py --with-interrupted` opts in.

**Still unknown:** the action name that actually reveals a Crystal Sphere
tile. All four guesses are rejected. The first fix above is what will name it
— the next time that screen appears, the log will carry whatever the mod says
about the rejection instead of `None`.

---

### One per-turn damage engine, and a deck small enough to draw it, 2026-11-13

Recommendation 2, both halves, in `bot/strategy/cards.py`. Grade **B** now, not
C: the direction holds in three independent sets, and the behaviour change
itself is grade **A** — a test, not an argument.

| | What it does |
|---|---|
| First engine jumps the queue | When the deck owns **no** damage engine, an engine on offer is the pick, ahead of the deck-relative quality bar. That bar judges a card against the median of the deck already held, which is the one comparison a first `Infinite Blades` always loses. |
| Flat picks damped past 20 cards | Each flat addition costs the engine draw frequency. Exempt: a card that *is* an engine, and a card that feeds one the deck owns — those make the engine better rather than rarer. |

The before/after, from the same offer and the same starter deck:

| Offer | Before | After |
|---|---|---|
| `Infinite Blades` / `Backstab` / `Deflect` | **Backstab** | **Infinite Blades** |
| `Noxious Fumes` / `Backstab` / `Deflect` | Noxious Fumes | Noxious Fumes |

Noxious Fumes was already taken on its own rating, so the change bites exactly
where the recommendation said it would: an engine whose intrinsic rating sits
below a flat alternative.

**`ENGINE_TAGS` was not reused for this, on purpose.** It counts every Power, so
`Footwork` (Dexterity), `Wraith Form` (Intangible) and `Well-Laid Plans`
(Retain) all satisfy it while adding no damage at all — and asking "does this
deck own an engine yet?" with the broad set lets a `Footwork` suppress the pull
toward the first real engine. `damage_engine_kind()` is the narrow test: a Power
or standalone payoff that also carries a damage-producing tag. It splits them in
two, because the distinction decides whether the card is an engine *in this
deck*:

* **self** — produces the damage it scales (`Infinite Blades` makes the Shivs,
  `Noxious Fumes` applies the poison). An engine in any deck.
* **payoff** — multiplies damage the deck has to supply. `Accuracy` with no
  Shivs is a dead card, so it only counts once an enabler is present.

Thirteen cards in the pool qualify; every Power it excludes is block, dexterity,
intangible, retain or draw. `Afterimage` is the one case the tag rule
over-reaches on (it scales Block) and is excluded by name.

**Engine density is now measured at floor 17**, which is what made this
actionable at all. Density at *death* cannot separate cause from effect — a run
that died on floor 8 never had the floors to find an engine — so the 4%/16%
split was partly just depth. `RunRecorder` now keeps the deck as it stood on
each act-boss floor (`deck_at_floor`, keyed `a1f17`), captured on the way past
because a run that loses the boss never reaches `game_over` holding that deck.
`damage_per_round.py` reports it as `dmg-eng@17`, with the number of runs it
rests on, and prints `--` rather than 0% for runs recorded before the snapshot
existed — a missing measurement is not a density of zero.

32 tests (`tests/test_engine_density.py`); 628 pass.

#### Measured, 2026-11-14 — it works and it does not help

Two 20-run sets on this build, back to back, no edit between them: sets 7 and 8.

| | pre-change (5+6) | post-change (7+8) |
|---|---:|---:|
| Average floor | 21.4 (+/-3.4) | 22.4 (+/-3.1) — **p=0.70** |
| Reached act 2 | 18/40 | 20/40 — p=0.82 |
| Runs owning >=1 damage engine | 14/40 (35%) | **27/40 (67%) — p=0.007** |
| Damage engines per deck | 0.35 / 0.55 | 1.15 / 1.95 |

**Grade A on behaviour:** at matched depth (21.4 vs 22.4), engine ownership
nearly doubled, and the floor-17 snapshot shows 26/31 runs holding an engine at
the act 1 boss floor. The code does exactly what it was written to do.

**Nothing on outcome.** Depth, act 2 reach and damage taken per floor are all
unchanged. The figure the change was built to move — act 1 boss losses at ~11
damage per round against the 14.8 that beat that boss — did not move: **9.7,
10.7, 11.1, 9.9, 11.4** across sets 4 to 8, flat through both post-change sets.

The one surviving fragment of support, from the floor-17 snapshot pooled over
sets 7+8 (n=31): owning **no** engine at the boss floor is clearly worst, 1/5
into act 2 against 19/26 for the rest. Beyond the first engine the pattern is
non-monotonic (1 engine: floor 29.5; 2: 22.4; 3+: 31.7), r=+0.32 p=0.066 — small-n
noise, and no argument for stacking. Grade C.

Kept, not reverted: it is a proven behaviour change with a coherent rationale and
no measured cost. But it is not an improvement, and nothing should be built on
the assumption that it was one.

### The Crystal Sphere is playable, 2026-11-13 — grade A for the protocol

The screen that ate the night of 11-13 can now be played rather than escaped.
The action names came from the mod's own help text, held as UTF-16 literals in
`<game>/mods/STS2_MCP.dll`:

> Use `crystal_sphere_set_tool(tool)` with `big` or `small`, then
> `crystal_sphere_click_cell(x, y)`.

**None of the four names the bot probed for a night exists in the mod.**
`crystal_sphere_click`, `_select`, `_reveal` and `_select_cell` each return
"Unknown action", and a tool was never selected at all, so no ordering of them
could ever have worked. The probe and its bookkeeping are gone with the
guesswork; `loop.py`'s unplayable-screen escalation stays as the backstop for
the next unknown screen.

What is and is not established, kept apart deliberately:

| Claim | Grade |
|---|---|
| These are the mod's action names and signatures | **A** — its own documentation |
| The payload shape (`tool`, `can_use_*_tool`, `clickable_cells`, …) | **A** — live fixture |
| The handler plays the minigame *correctly* | **unverified** — no sphere screen has come up since |
| `big` over `small` | **C** — a judgement; three divinations, and the binding constraint on this bot is output rather than survival. One constant to flip |

The method generalises and is the part worth keeping: `shop`, `rest_site`,
`treasure` and `bundle_select` are all still marked "built on defensive field-
name guesses" in `README.md`, and every one of them can be settled by decoding
that DLL as UTF-16LE instead of by losing a run. See `KNOWLEDGE.md`.

`tests/test_crystal_sphere_stall.py` was retargeted from the probe to the
protocol; 634 pass.

## Done — infrastructure (not gameplay)

| Change | Evidence | Where |
|---|---|---|
| Test suite can no longer force-kill the real game | **A** — `test_loop.py` drives `run()` from 7 places unstubbed; with `time.sleep` patched out, one pytest run fired 7 `steam://rungameid` requests in 3s. This was the "game keeps restarting on its own" mystery. Verified 0 relaunches/run, was 7 | `tests/conftest.py` |
| Test suite can no longer write to the real stats file | **A** — 54 synthetic rows vs 21 real runs, reporting avg floor 6.4 against a true ~13 | `tests/conftest.py` |
| Every game relaunch logs its caller | **A** — needed to catch the above | `recovery._note_relaunch` → `stats/relaunch_log.txt` |
| Shadow recorder + analyzer for human runs | — | `scripts/shadow_record.py`, `scripts/shadow_analyze.py` |
| Analyzer no longer counts one declined suggestion as N disagreements | **A** — inflated elite disagreement, reported 13% agreement when it was 56% | `scripts/shadow_analyze.py` |

---

## To do — ordered

Set 4 reordered this list. The evidence for the first four is in
[`REPORT_20260930_recommendations.md`](REPORT_20260930_recommendations.md), and
the standing constraint has moved with it: **floor 17 is close to saturated**
(14 of 16 in set 4, only 2 runs dying before it), and what remains are
damage-output losses. So work belongs in `cards.py`, `shop.py` and `rest.py`,
not in the combat pipeline — `combat.py` is where nearly every past bug came
from and damage taken per floor is the best it has ever been (6.1).

### 1. Damage per round, as the primary metric — grade A
Floor depth cannot resolve the changes now being made: set 3 (19.2 +/-3.9) and
set 4 (23.2 +/-4.4) overlap after 36 runs between them. Damage dealt per round
in the deciding fight separates the outcomes completely — **27.8** mean for runs
that reached act 2 against **9.6** for runs that lost the act 1 boss, with no
overlap at all between the groups — so a deck change can be judged on roughly 8
runs instead of 20+. Compute it per run from the decision log (each named
enemy's `max_hp - hp` over the fight, divided by rounds) and report it beside
average floor and damage per floor. It is a leading indicator, not the goal:
keep damage per floor next to it, or a change that raises output while dying
sooner will look like progress.

### 2. Recover interrupted runs into `runs.jsonl` — grade A
A run interrupted before `game_over` never reaches `RunRecorder.finish`, so it
exists only in the decision log and is invisible to every analysis, all of which
start from `runs.jsonl`. Two have been lost this way, and one held the most
informative fight of set 4 — Soul Fysh **killed**, which is the evidence that
the bar is 1.7x current output rather than 3x. Either checkpoint a provisional
record periodically, or reconstruct one from the decision log when no run covers
its time span.

### 3. ~~One engine beats another flat card~~ — **built 2026-11-13**, see above
Implemented on `next-set-fixes`; waiting on set 6 to finish so there is a
baseline to measure it against. The fight that motivated it did not come close,
it **won** — see the correction above.

### 4. Soul Fysh's Intangible cycle — grade A
Its kit, from the payloads:

    Attack intents      7, 10, 13, 16, 18, 24   (escalates through the fight)
    StatusCard intents  "give you 1 Status card", "...2 Status cards"
    Self status         Intangible 1 -- "Reduce all damage taken and HP loss
                        to 1. Lasts for 1 turn."
    Applies             Weak 1 to us
    HP                  211

Verified across six logged fights: **Intangible lands on rounds 5, 10, 15 and
20** — every occurrence is a multiple of five, 17 of 96 rounds, 18% of the
fight. `_playable_hand` already declines to attack into it (verified live, set 4
run 5), but the bot does not *plan* for it: those rounds should be spent on
Block, draw and setup with burst held for the rounds either side.

Check first whether poison is also reduced to 1. The status text says it caps HP
loss, which suggests yes — if so, poison ticks want timing off-cycle; if not,
poison is the counter to this boss and item 3 should prefer poison engines
specifically. That answer belongs in `KNOWLEDGE.md` before any code change.

Damage escalating 7 → 24 means the fight cannot be allowed to drag, which pulls
against stalling: stall only on the turns Intangible is actually up.

### 5. Hand clog: value outlets higher as unplayable cards accumulate — grade B
Still live, and it serves two enemies at once. Wrigglers add `Infection` every
other turn and Soul Fysh deals 1-2 Status cards a turn; with three or four
generators the hand clogs faster than a starter deck clears it. At the decisive
logged turn the bot held four Infections and one Strike — it did not choose not
to block, it had nothing to block with. The outlet promotion of 2026-08-29 was a
first pass and was never measured on its own.

### 6. Floor 7-9 elite wall — grade B (measured on `act1_classic` only)
0% of deaths before floor 7; **45% at floors 7-9** (n=31). Byrdonis x3, Bygone
Effigy x3, Wriggler packs x4. Set 4 saw only 2 of 16 runs die before floor 17,
so the route-projection and pre-boss heal work may have overtaken this —
re-measure before acting on it.

### 7. Shop policy: removal vs relics — grade C
The bot picked `card_removal` in 4 of 8 shop divergences and potions in 2; the
human bought relics every time (Eternal Feather, Cloak and Dagger, Red Mask,
Dolly's Mirror) and reached floor 48. One run, so a hypothesis only.

### 8. Elite potion behaviour — grade C, do not act yet
The bot opens every elite wanting a potion and holds that view all fight; the
human drank none and reached floor 48. **Contradicted** by an earlier live
observation of the bot dying with three unused potions, and the
aggressive-in-elites rule exists by explicit request. Needs a second human run
or a controlled A/B before touching. Open from the same record: two potions
spent in one turn at 16/70 HP, the aggressive-at-low-HP rule firing as designed.

### 9. Map routing — grade C
Nothing was ever recorded under this heading beyond the title.

### Resolved

Full write-ups are in the Done sections above and in git history.

| Was | Closed |
|---|---|
| `_self_damage_in_hand` misses "lose N HP" (1a) | 2026-08-29 |
| Bubble Bubble played into unpoisoned enemies (1b) | 2026-08-29 |
| Hardened Shell / damage caps (2) | 2026-08-28 |
| Wriggler Infection clog, the outlet promotion (4) | 2026-08-29 — but the priority problem is item 5 |
| Extend the shadow analyzer (5) | 2026-08-28 |
| `card_select` picks by static score (9) | 2026-08-29 |
| Forced discards ignore Retain (10) | 2026-08-29 |
| Thorns is invisible to the bot (11) | 2026-08-29 |
| Measure the fixed build (old item 1) | superseded; five sets have been measured since |
| Soul Fysh, how to fight it (old item 15) | quantified as item 4 and in the recommendations report |

---

## Standing lessons

* **A 20-run set cannot resolve a change, and a p-value between two sets is
  noise.** Sets 7 and 8 ran on one identical build with no edit between them and
  differ at floor p=0.015 — while the change they shared, measured on 40 runs
  each side, gives p=0.70. Set-to-set variance is larger than anything being
  tested for. Set 7 alone looked like the first real improvement in the
  project's history; set 8 erased it. Compare **pooled runs across sets on the
  same build**, never one set against one set, and treat any single set's
  headline figure as a draw from a wide distribution. Set 5 was the warning
  (24.1 with none of the change) and was nearly explained away at the time.
* **A growing log is not a working bot, and a watchdog must print what it
  measured.** The supervisor's stall guard required the API to be unreachable
  *and* the decision log to have stopped growing. When the game wedges the bot
  writes a `get_state_error` record every eleven seconds, so the log grows
  steadily and `static_for` never leaves zero — the guard was blinded by the
  exact symptom it existed to catch. It watched a dead API for forty minutes on
  2026-11-19 while printing `bot alive, API up`, which was *literal text* rather
  than the values it had just read. Any liveness signal that the failure itself
  produces is not a liveness signal; and a heartbeat that cannot say `DOWN` is
  decoration.
* **Some states no relaunch can fix, and the watchdog must name them rather
  than retry.** Second instance in two days: a mod that will not load
  (2026-11-18) and a main menu locked behind a manual unlock reveal
  (2026-11-19). Both are indistinguishable from "slow" if you only watch from
  outside, and both cost a night before being named. When a restart cannot
  change the outcome, stop and say which human action is required.
* **`can_confirm` means "confirming is legal", never "enough is selected".**
  Three screens have now been caught assuming otherwise (retain prompts,
  multi-pick `card_select`, Enchant). Assume any new selection screen is
  guilty until a live payload proves otherwise.
* **Check a surprising measurement against raw records before believing it.**
  Four analyzer artifacts have masqueraded as findings: elite agreement
  "13%" (really 56%), rewards "22%" (click order), rest sites "60%" (really
  100%), and a map divergence that was the analyzer pairing the wrong record.
* **Read-only is not the same as harmless.** A GET-only recorder froze the
  game twice by polling too hard.
* **Helpers get written and left unwired.** `_debuff_is_wasted`,
  `_thorns_cost` and `_mitigation_value` were all defined before being
  called. Grep for call sites before claiming a fix works.
* **Key names differ between the raw payload and the logs** (`description`
  vs `text`). Two wrong conclusions came from analysing the logged shape.

---

## Decided against

| Rejected | Why |
|---|---|
| Raising `EARLY_BAR_FACTOR` to take more cards early | The early-intake shortfall was the upgrade bug, not the threshold: **8 of 11** early skips were offers where *every* card was upgraded and therefore scored 0.0. Raising the bar as well would have compounded into an over-correction — and would have re-made a mistake already made and reverted once in this project. |
| Auto-abandoning a non-zero-ascension run | Reaching the main menu requires relaunching the game. If the ascension were sticky that would relaunch forever. Flag loudly and finish the run marked instead. |
| `_is_defensive_only` in the value step | Kept Block-plus-Weak cards (Leg Sweep) out of the *value* step when nothing was winding up, so a free turn went to damage. Reverted: the set it ran on had damage **per floor** go the wrong way (5.72 -> 6.57) and deaths below floor 13 go 0% -> 22%. Investigation then showed the original code already handled the reported case — the old rule only fired when *nothing* was attacking, so it could never have caused "2 Defends instead of Leg Sweep". **A fix for a non-bug that caused a regression.** |
| `_defence_already_covered` in the value step | Skipped a Block card when the hit was already fully blocked and the card's mitigation was under `MIN_MITIGATION_VALUE`. Aimed at one real pattern (37 Block banked against 5 incoming with three Strikes unplayed) but measured on the set it fired on **26.2% of all turns** — by far the widest reach of any unmeasured change in that build, which scored 13.9 against a 20.9 baseline. Retry only against a controlled comparison. |

---

## Open questions

- ~~Does an ascension control appear in the API payload on character-select?~~
  **Answered 2026-08-28: no.** See priority 1. It is a manual setting; the
  tripwire and the stats filter are the net.
- Gold rewards cap at +5.0 (reached at 100 gold), so 150 and 600 gold score
  identically. Deliberate cap, but probably too low now that card offers can
  reach +10.5. Needs evidence before tuning, not a guess.
- The bot cannot see the map when choosing at Neow: that state carries only
  `['event', 'player', 'run', 'state_type']`, and the order is
  `event(NEOW) → card_select → event(NEOW) → map`, so there is nothing cached
  either. Routing to a shop to remove an accepted curse is not possible at
  that moment — and in the logged act the nearest Shop was row 11 of 15, so it
  would not have helped anyway.
- Is the deck-size ↔ depth correlation (+0.77 real cards vs floor) causal, or
  just that deeper runs collect more cards? Partly circular; do not cite as
  causal.
