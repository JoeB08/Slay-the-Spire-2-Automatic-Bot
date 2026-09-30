# Changes — needed, in progress, and done

Living document. **Update it in the same edit as the code change**, not
afterwards. Every entry says what the evidence was, because the recurring
failure mode on this project has been acting on a single observation and
over-correcting.

Companion files: `KNOWLEDGE.md` (how the game and API actually behave,
permanent), `CURRENT_WORK.md` (session state / where things stand).

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

## Done — but NOT yet measured

### Staged during set 4 (2026-09-11) — on `next-set-fixes`, not in its build

Set 4 runs on the batch below; these wait for the set after it.

* **A dangerous route is not a dead one — grade A, reported by the user.**
  Set 4, run 1, floor 7: 12/70 HP between two Monsters, one leading to a
  rest site. The new route projection scored anything under 10% of max HP as
  death and stopped looking, so both options scored -100 and the tie went to
  the first; the run died two floors later. Death is now a projected 0 HP;
  under 10% a route takes -50 but keeps its continuation, so the rest site
  beyond the fight wins. Map decisions are not replayed (the graph is not
  logged). `d375995`.
* **Attack on the Lagavulin Matriarch's last asleep turn — the user's call.**
  The filter that holds attacks while every enemy sleeps also held them on the
  Asleep-1 turn, when the boss wakes anyway; the leftover step then played
  Block against a boss that was not attacking. Set 4, run 3 and all 12
  recorded Matriarch fights spent that turn on Block. `ccb0e37`.
* **The relaunch loop — grade A.** The bot is meant to give up on a run that
  freezes again right after being resumed (`ABANDON_AFTER_N_RECOVERIES`), but
  every successful action reset the recovery count -- including the main
  menu's "continue", which succeeds and then loads straight back into the
  freeze. Set 4's start looped this way on the unhandled fake merchant. A
  main-menu click no longer counts as progress.
* **Resume a paused run — the user's call.** Start-up still abandons a run in
  progress so a set measures one build, but `bot_control.ps1 -KeepRun` now
  continues it instead (`STS2_KEEP_RUN`).
* **The whole map in the log.** Map rows carried only the next options, so no
  routing decision could be replayed -- including the one the dangerous-route
  fix is about. `_raw_for_replay` now keeps the map payload.
* **Decimillipede segments revive — grade C.** Reattach reads "If other
  segments are still alive, revives in 2 turns with 25 HP": a segment killed
  far ahead of the others comes back before they can follow. Damage that does
  not kill now goes to the healthiest segment so they fall together, and a
  segment kill is skipped while another has more than its revive HP -- unless
  the hit coming would kill us. One fight of evidence (set 3, run 13).

### Batch of 2026-09-11 — built from set 3's findings, on the user's go-ahead

Set 3 (19.2 +/-3.9, act 1 boss 15/20) showed no change from the batch before
it; these target where its runs actually ended. Together they change 353 of
34,772 archived decisions. Map, rest, shop and card-reward decisions are not
in the replay (their payloads are not logged), so those rest on tests alone.

* **No kill that leaves a lethal hit coming — grade A.** The lethal step
  played any killing card before the survive step, never asking whether we
  lived. Run 15: 6 HP, the last energy on Strike to finish a Tough Egg whose
  intent was Summon, 9 still came through, Defend in hand. A killing card is
  now skipped when what still gets through after the kill is lethal; a
  leader's kill and the last enemy's are exempt. 22 distinct recorded turns
  fit, 6 of them ended the run. 9 decisions change.
* **HP carried along the route; heal before the boss — grade A.** The map
  lookahead scored every node with today's HP. Each route now carries
  projected HP (p75 fight costs: act 1 Monster 9, Elite 38, Unknown 5), and
  a route that drops below 10% of max HP is worth -100. Runs 4 (Monster over
  RestSite at 4/70 for a shop beyond) and 7 (Elite at 43/70 onto two elites
  and no rest) both go the other way. The map screen leaves its graph in
  `route_memory`; the rest site heals when the boss is next or when the best
  route would reach the next rest below 15% HP. Set 3's pre-boss rests:
  Smith 8, boss beaten 2; Rest 7, beaten 4 from far lower HP.
* **Giant potions wait for its death blow — grade A.** While the Waterfall
  Giant has real HP, only damage and healing potions are spent unless this
  turn's hit would kill us. 160 decisions change.
* **Skittish counted — grade A.** From the bot's own hits this turn, since
  the payload rarely shows the Block. 50 decisions change.
* **Smoggy and Gas Bombs — grade B.** One Skill in the block plan; no chip
  into a bomb that explodes anyway. 7 decisions change.
* **Sly draw/energy cards not paid for — grade B.** Reflex and Tactician.
  25 decisions change.
* **Draw first on a dying turn — grade B.** Only when the Block shortfall
  would kill us, with energy to spare, never under a play or Skill cap. 91
  decisions change; unproven on the original run (draw order is not logged).
* **Accuracy in card rewards — the user's call.** A payoff card for an
  engine the deck feeds (`cards.is_key_payoff`) comes first, as in the shop.

### Fix after the merge (2026-09-11) — found in set 2's run 4

**A far-off leader drew the chip damage — grade A, my regression.** Leader
priority sent damage that does not kill to the leader of a Minion fight even
when the leader was nowhere near dying. Set 2, run 4: Kin Priest (190 HP) with
two attacking Kin Followers -- replayed, the pre-merge code aims 2 attacks at
the Priest and 16 at the Followers, the merged code 8 and 10; nothing died in
eight rounds and the run ended there. The rule was written for Ovicopter,
which re-summons its minions; Kin Followers do not come back. Chip now goes to
the leader only when it is in reach, summoning (`_resummons`), or none of its
minions is attacking -- chip on an attacking minion becomes a kill that
removes damage. The Kin fight replays exactly as the old code played it. 32
of 25,731 decisions change against the merged build, 18 of the first 20 shown
being Kin Priest -> Kin Follower. 479 tests.

**Echoing Slash's chain — the user's call.** "Deal 10 damage to ALL enemies.
Repeat this effect for each enemy killed." Nothing read the repeat, so it was
valued as one 10-to-all. Live, floor 23: Echoing Slash+ (18) sat in hand
against three Tough Eggs on 18/17/16 and Ovicopter on 73 -- the chain kills
all three eggs and puts 72 into Ovicopter, leaving it on 1 -- while Murder+
went into Ovicopter. `_chain_kills` simulates the waves; `_step_chain_kill`
plays such a card first whenever it kills now against two or more enemies
(not when the hit would kill us, unless the chain ends the fight).

**Tainted — the user's call.** Infested Prism's Vital Spark makes every Skill
Tainted: playing one adds Tainted, "Take N additional damage from Attacks this
turn", stacking to 16 in one live turn. Now: Tainted already taken counts on
every incoming attack hit; a Skill's Block is counted net of the Tainted it
adds; and a Tainted Skill is a dud while an attack is coming unless its own
Block, or the Block already up beyond the hit, covers what it adds. A first
version ignored spare Block and withheld Prepared with 23 Block against 19;
narrowed. With the chain step: 25 of 27,323 archived decisions change against
the master build. 488 tests.

**A key card for the deck's engine comes before a shop relic — the user's
call.** "I want the card within the archetype to come first if graded as a
strong card, like how accuracy is very strong with all the shiv." Set 3, run
1, floor 29: the relic-first order spent 153 gold on Blood Vial and a removal,
leaving 12 for a 39-gold Accuracy in a deck of Shiv makers. `score_card`
cannot grade this -- it rates Accuracy 36-67 against Prepared's 116 -- so the
rule reads the card data: a `shiv_payoff` or `poison_payoff` card, with 2+
matching enablers already in the deck and fewer than 2 copies held, is bought
right after a shop-discount relic and before everything else. Shop decisions
are not replayed by `decision_snapshot.py`, so there is no replay count. 490
tests.

### Batch of 2026-09-10 — staged on branch `next-set-fixes` while the set ran

Every fix was first reproduced against the exact live payload (eight are now
fixtures, `tests/fixtures/live_*.json`), then replayed across the 16,228
archived decisions: **2,454 change, and every one traces to a fix below —
none to anything else.** 2,157 of those are potion rows, but the snapshot
judges each turn alone, so a potion held through a fight counts on every
turn; collapsed, they are **under 90 fights**, each one potion drunk once that used
to be carried until the bot was dying. The rest: 101 random-target kills, 73
leader priority, 58 Sly kept, 28 Block plan, 19 incoming damage, 11 death-blow
turns, 5 Fan of Knives, 2 Storm of Steel. 476 tests green, 27 new. Kept off master until the running set finished, so
it could not leak into that measurement.

**The loop ended turns with playable cards — grade A, user-reported.** The
cycle detector counted a window of 8 near-identical *decisions* as a loop and
never looked at the state. Each Shiv slides into the same hand slot, so "play
card 2" repeats while the hand shrinks and enemy HP falls -- real progress,
read as stuck. It forced an end_turn with cards in hand 7 times in one set,
once at the act 2 boss; replaying those payloads, `decide()` itself says "play
the Shiv". The window now keys on (decision, state), so a true ping-pong,
which repeats its states, is still caught. Loop test drives eight Shivs.

**Card potions were only drunk when dying — grade A, user-reported.** The
rules recognise damage, Block, heal and draw/energy; "Choose 1 of 3 random
Power cards to add into your Hand" matches none of them, nor does Entropic
Brew. A run carried a Skill and a Power Potion through all 19 rounds of the
act 1 boss and Entropic Brew from floor 6 to 24, then drank four at once on
14 HP. Card potions now count as utility in elite/boss fights and at low HP
(not into a full hand); Entropic Brew is drunk whenever a slot is empty.

**Sly Block cards are kept for a free discard — grade A, user-reported.** 12
unblocked at 30 HP with Untouchable, Defend and two Strikes: the bot paid 2
energy for Untouchable. When the non-Sly Block leaves no more than
`SLY_KEEP_MAX_CHIP` (5) through and HP allows, that chip is taken instead.
Never under The Gambit.

**Fan of Knives first against a crowd — the user's call.** With two or more
enemies standing it is played ahead of the lethal step (not when the hit
would kill us). Floor 9, three enemies: Pounce killed a 9 HP slime that four
all-enemy Shivs would have killed anyway, with 16 into each of the others.

**A hand-discard whose discard is the payoff is not a dud — grade A.** Run
13 died 8 HP short of an Entomancer: on 3 HP, holding Haze (Sly) and Storm of
Steel ("Discard your Hand. Add 1 Shiv into your Hand for each card
discarded."), it paid 3 energy for Haze. Storm would have made four Shivs and
played Haze free. The rule that holds hand-discards until the energy is spent
(written for Shadow Step, where the discard is only a cost) had written Storm
off as a dud because Haze was affordable, so no step ever saw it.
`_discard_is_the_payoff` exempts it when a Sly card would be discarded or the
Shivs can kill. 2 archived changes.

**The Waterfall Giant's last turn is spent on Block — the user's call.** At 0
HP the Giant becomes a 999,999,999 HP husk whose only intent is a DeathBlow;
the fight ends after it. Damage into it does nothing, so on that turn cards
whose only value is damage are duds, chip is never accepted, and Sly Block is
not held back -- there is no later turn. Run 1 spent its last turn attacking
the husk. `_is_spent` requires the husk's placeholder HP (`HUSK_MIN_HP`): Gas
Bomb shows the same DeathBlow intent with real HP (7/7) and killing it may stop
the blast, so it stays a normal target -- a first version that matched the
intent alone changed 54 Gas Bomb decisions and was narrowed. 11 archived
changes, all Giant.

**Random-target damage is not a promised kill — grade A.** Run 20, round 5:
31 incoming at 56 HP. Ricochet ("Deal 3 damage to a random enemy 4 times") was
counted as 12 into a 13 HP Cultist, so Strike + Ricochet "killed" it and the
kill-the-attacker step skipped blocking. 3 landed; the Cultist lived on 4 and
the bot took all 31 (56 -> 25), dying two floors later. The lethal and kill
steps now count random-target cards toward a specific enemy only when one
enemy is left (`_certain_on`) -- the rule Serpent Form's per-play damage
already followed. 101 archived changes.

**Two incoming-damage calculations disagreed — grade A, the widest of these.**
`_incoming_damage`, which the survival math and the potion emergency check
read, kept its own `type == "Attack"` whitelist. The DeathBlow and Back Attack
fixes had both landed in `_enemy_attack_damage` and never reached it. Across
19,445 logged turns the two disagreed on 305: 135 death blows read as **0**
(Waterfall Giant's final 51, Gas Bomb's 8) and 170 back attacks at
two-thirds. It now delegates. This is also why a run died on the Giant's last
turn holding a Colorless Potion: `about_to_die` saw 0 incoming. 34 archived
changes (21 back attack, 13 death blow).

**Most Block for the energy — grade A, user-reported live.** With no single
card covering the hit, step 2 played the biggest single Block card whatever it
cost: Untouchable (2 energy, 6) over Defend (5), spending both energy on 6
when two Defends make 10. Live: 15 incoming, three Defends in hand, took 9
instead of 0. `_best_block_plan` tries every affordable set; the biggest card
of the set covering the most (cheapest on ties) goes first. One archived
change is a death averted -- Soul Fysh, 4 HP, 13 incoming: Untouchable's 6
becomes Cloak and Dagger + Defend's 11. 59 archived changes.

**Normality caps plays, not just energy — grade A.** "You cannot play more than
3 cards this turn. (3 cards left)" is a Curse *in hand*; `_play_limit` reads
statuses only. `_plays_left` reads it and the Block plan respects it. Nothing
else does yet -- run 6 spent all three plays on attacks against 24 incoming.

**Leaders first, minions not ignored — grade B, the user's call.** The first
version of this batch excluded Illusion enemies from targeting outright,
reasoning that Eye with Teeth revives at full HP, so killing it buys nothing.
**That was wrong**, and the logs said so: killing it on our turn stops its 3
Status cards (216 logged turns ended with it dead: 0.31 cards added on
average; ended alive: 2.5). Replaced, per the user ("don't completely ignore
minions, but have some priority to target leaders"), with:
  * the leader is read from the `Minion` flag ("Minions abandon combat without
    their leader") -- `_is_leader`; `_is_likely_leader` guessed from names and
    had been calling Ovicopter's Tough Eggs leaders;
  * when this turn's damage, repeated, kills the leader within
    `LEADER_FOCUS_TURNS` (2), neither the lethal nor the kill step chases its
    minions -- run 10 spent ten rounds killing eggs with Ovicopter at 51 and
    Strikes landing for 20+, and died;
  * damage that does not kill goes to the leader;
  * otherwise minion kills stand -- they deny an action.

**Removed at the user's request:** the Lagavulin poison-on-offer rule from the
first version of this batch. `boss_intel` and its Block bonus are unchanged.

**The Gambit — grade A, rare.** "If you take unblocked attack damage this
combat, die." Chip is never acceptable while it is up, and the card is never
picked over an alternative -- as an unknown card it tied Stratagem at 30 and
won on list order. One run in 124, dead at 69/70 HP on floor 3.

**Deliberately not in this batch:** Waterfall Giant's death blow (every loss
was already unwinnable on the final turn; the levers are HP entering the fight
and HP-over-damage inside it, a behavioural change to measure on its own),
scaling enemies (needs a model), Decimillipede's Reattach, Normality beyond
Block, removals (check the set's starter counts first), the Distilled Chaos
freeze (not yet reproduced), and "Free Skill" showing every Skill at cost 0
(self-correcting after one play).

### Batch of 2026-08-29 (late) — staged while the 20-run set finished

All written against evidence from that set's own logs. 418 tests green.

**The skip-memory leak — grade A, the largest defect found on this project.**
`rewards.decide_rewards` filtered a card off the rewards screen whenever it
had once skipped a card at that `(act, floor)`, and the memory never cleared
between runs. Measured on the set's log: **46 cards abandoned unopened against
43 taken — 46% of every card offer**, and 46 of 46 at a floor where a real
skip had been recorded. Eleven genuine skips poisoned those floors for the
rest of the session; floor 2 was skipped once and abandoned eleven more times.
Fixed by scoping the memory to the run (`_forget_skips_on_new_run`). Audit it
with `scripts/card_rewards_audit.py` — it should report zero.

**"Lose N HP" is unblockable — grade A** (was priority 1a). Beckon's HP loss
was being summed with Infection's "take N damage" and answered with Defend.
Block now sizes itself against the blockable portion only, while survival math
still counts the whole thing. `_unblockable_self_damage` / `_hand_hp_loss`.

**Held Status cards get played away — grade A** (the other half of 1a). Beckon
is `can_play: True` at 1 energy, so playing it simply deletes the damage; that
beats a Defend outright when the loss is unblockable. New step 1.6, guarded so
it never spends a block we need to survive the turn.

**Capped turns (Ringing) — grade A, user-reported.** "You can only play 1 card
this turn" was entirely unmodelled. The turn is now a single decision: biggest
defensive body, and never a Shiv (0-cost attacks the bot would otherwise dump
for free, each burning the whole turn). Detected by status name *and* by
effect text, since no live payload has been captured. `_play_limit`.

**Hand clog — grade B** (was priority 4, and half of priority 15). Once two or
more Status/Curse cards are in hand, an outlet that removes them outranks its
printed score and bypasses the value gate. Serves Wriggler (in both Act 1
pools) and Soul Fysh equally. `CLOG_THRESHOLD`, `_clears_clog`.

**Relics feed archetype detection — grade A.** Relics were invisible to it. A
live run held Snecko Skull while committing to shiv with poison at 0.60.
Matched on description text, plus a name table and a mechanic pattern for the
relics whose text never names their archetype — **Kunai and Shuriken read "3
Attacks in a single turn", which is a shiv relic in practice** (user
correction). Weighted so a relic breaks a tie but never commits a build alone.

**One engine card is a direction, not a build — user correction.** Noxious
Fumes scored 2.55 and cleared the weight threshold by itself, committing the
whole run to poison off a single pick. Commitment now also requires two
on-theme cards (`ARCHETYPE_MIN_CARD_COUNT`). Relics cannot substitute for the
cards.

**Potion magnitude — grade A.** `_accomplishes_nothing` only refused a Weak
potion when *nothing* was attacking, so a Weak potion against a 4-damage hit
passed the gate and prevented 1 damage. It now requires the reduction to be
worth roughly a Defend (`MIN_MITIGATION_PAYOFF = 5`, i.e. 20+ incoming), which
still fires against Soul Fysh's escalating 24 and holds against chip damage.

**Shop order is relic -> removal -> cards — user-specified.** Was removal ->
relic -> cards. A shop relic is a one-time offer that leaves with the shop;
removal recurs at later shops and the junk stays removable. Reported live as
"why did it buy war paint, it should have bought horn cleat, removal and then
cards". `test_buys_card_removal_first` was passing only because every relic in
the fixture is unaffordable -- renamed to say what it actually asserts, and
the ordering is now pinned by two new tests.

**Discard tier 4 agrees with the purge step — grade A.** Tier 4 ranked
self-damaging held cards by magnitude alone, so it pitched Beckon (6 HP,
`can_play: True`) and kept Infection (3, Unplayable). That is backwards:
discarding is Infection's *only* outlet, while Beckon costs one energy to
delete. The discard now goes to whichever card cannot be purged, and magnitude
only decides between two equally unplayable ones.

**Sequencing consolidated — the structural fix, deferred four times.** Four
bugs were one defect: `_play_timing` ranked a card correctly but an earlier
step in `decide()` committed the turn before ordering was consulted. Choke
lost to the free-damage step, Flechettes to the block step, Backflip to the
value step, the clog outlet to the block step. Three bespoke guards are
replaced by one rule (step 1.4, `_loses_value_if_deferred`) with one shared
guard: reorder only while the block we still need stays affordable and the
incoming hit will not kill us. Survival always wins the tie.
`tests/test_play_sequencing.py` pins it.


These are live in the code and covered by tests, but no run set has been
completed on the build containing them. Their effect is unknown. The next
clean 20-run set exists to measure exactly these.

| Change | Evidence | Where |
|---|---|---|
| Upgraded card names resolve (`Afterimage+` scored 0.0, now 89.0) | **A** — bot took 0 of 14 upgraded cards ever offered | `cards.base_name`, `card_info` |
| Already-upgraded cards excluded as upgrade targets | **A** — picked `Strike+` for an upgrade effect, gaining nothing | `cards.pick_upgrade_target` |
| Curses detected via `rarity` as well as `type` | **A** — live removal screens don't always send `type` | `cards.removal_priority` |
| Between identical starters, cut the un-upgraded copy | **A** | `cards.removal_priority` |
| Retain prompts actually retain | **A** — 41 retain prompts in one run, 0 cards retained | `misc_screens.decide_hand_select` |
| Sly pitches ranked by realised value this turn | **A** — pitched a Sly attack the enemy's Block ate, kept a Sly block card | `misc_screens._sly_realised_value` |
| Defensive potions not spent on threats this turn's cards kill | **A** | `potions.suggest_potion_use` |
| Ascension tripwire (`unexpected_ascension`) | **A** | `loop._check_ascension` |
| Unknown cards score middling, not 0.0 | **A** — an unfamiliar card was the first thing discarded and unpickable as a reward; the new Act brings cards the data file has never seen | `cards.UNKNOWN_CARD_QUALITY` |
| Quest cards never removed from the deck | **A** — `Spoils Map` marks 600 gold; `_is_dead_weight` missed it (type `Quest`, `can_play` null) | `cards.removal_priority` |
| HP-threshold stuns are played for | **A** — Terror Eel's Shriek stuns it at ≤70 HP, cancelling a full turn of damage; the bot treated 71 and 69 as identical | `combat._stun_threshold`, step 1.15 |
| Unknown attack intent types counted as damage | **A** — Waterfall Giant's `type: "DeathBlow", label: "48"` was not the string `"Attack"`, so incoming damage read **0** and the bot died to a 48-hit it could see. Now a denylist of harmless types, plus a prose fallback | `combat._enemy_attack_damage` |
| Intent hit count read correctly | **A** — labels are damage-by-hits ("5x3" = 5 damage, 3 times, confirmed against the prose). Needed by every per-hit calculation | `combat._enemy_hit_count` |
| Mitigation valued by damage prevented | **A** — was a yes/no flag; Strength reduction applies per hit, so Piercing Wail covers 6 vs one attack and 15-18 vs three | `combat._mitigation_value`, step 1.7 |
| Thorns charged per hit | **A** — `grep -i thorn bot/` returned nothing; Toadpole carries Thorns 2 and the Silent's multi-hit kit pays per hit | `combat._thorns_cost` |
| Discards credit two-for-one cards | **A** — Cloak and Dagger ("Gain 6 Block. Add 1 Shiv") classed attack-only, so its Block was invisible and it was pitched over a 5-Block Defend | `misc_screens._discard_rank` |
| Discards prefer pitching non-Retain cards | **A** — a card without Retain is discarded at end of turn anyway; the ranker had no Retain term at all | `misc_screens._has_retain` |
| `card_select` scores by this-turn value in combat | **A** — took Knife Trap at 16 HP with an empty exhaust pile, which the game itself labelled "(Plays 0 Shivs)" | `misc_screens.decide_card_select` |
| Combat detail logged on selection screens | **A** — `hand_select` logged incoming/block/unblocked as None, the exact numbers needed to judge a discard | `loop._combat_detail` |
| Deck pile *contents* logged in combat | **A** — only counts were kept, so archetype could not be measured at a fixed point | `loop._available_options` |
| Per-turn HP-loss caps are respected | **A** — Skulking Colony's `Hardened Shell`: "cannot lose more than 20 HP each turn". Damage above the remaining allowance is discarded, and a kill needing more than it is impossible this turn. The bot previously committed whole turns to both | `combat._damage_allowance`, `_capped_damage`, `_kills_enemy`, `_ranking_damage` |
| Serpent Form counted toward lethal | **A** — "Whenever you play a card, deal 6 damage"; with it up and an enemy on 6 HP, *any* card kills, but lethal counted only the card's own damage. Only credited against a single enemy, since the effect picks randomly | `combat._per_play_damage`, `_kills_enemy` |
| Status cards can't outrank attacking | **A** — regression from unknown-card scoring: `Slimed` ("Draw 1 card. Exhaust.") is unknown to the data, so at 30 it cleared the value step's >=20 gate and whole turns went to exhausting Slimed | `combat` value step |
| Artifact recognised; Expose played first | **A** — "Negates 2 debuffs" was invisible, so Weak/Vulnerable/Poison were fed into it and eaten. Expose ("Remove all Artifact and Block... Apply 2 Vulnerable") now sorts `PLAY_EARLY`, ahead of the debuffs it enables | `combat._enemy_artifact`, `_debuff_is_wasted`, `_play_timing` |
| Attacks that can't break Block are discounted | **A** — a 6-damage Strike into 7 Block while the enemy wound up a 13-damage hit. Block resets, so absorbed damage is gone. Applies in the leftover-energy scan, where the misplay actually originated; Poison exempt | `combat` leftover-energy rank |
| Archetype weighted by card strength | **A** — counting tagged cards made Noxious Fumes (pick rate 68, the poison engine, a Power) worth the same as Deadly Poison (23), so owning the build-defining card wasn't enough to commit | `cards._archetype_weight` |
| Normal fights outrank elites until the deck is built | **B** — `Monster` scored 0.5 vs `Elite` 6.0, so the bot ran at elites on a starter deck; 45% of deaths are floors 7-9 with a median deck of 14 | `map_nav._monster_value` |
| `deck_memory` stores the true deck | **A** — it stored every pile, so of 181 "chosen" cards 154 were tokens (`Infection` x80, `Shiv` x16). That fed archetype detection, synergy counts, removal copy-counts, and the intake bar (a median over the deck) | `deck_memory`, `GameState.true_deck_names` |
| Card scoring reads what a card does | **A** — `score_card` used community pick rate and nothing else, so a starter Strike (default 30) outscored `Pounce+` (20 damage, 25) and `Precise Cut+` (16 damage for 0 energy, 19). The bot skipped all three from a bare starter deck, and kept skipping rewards at floors 30 and 35. Now blends pick rate with damage/Block/poison per energy. Gated damage ("Can only be played if…" — Grand Finale scored **180**) counts zero; variable damage ("for each…") is halved; the blend only ever *raises*, since utility cards like Prepared and Footwork have no parseable numbers and a straight blend halved them | `cards.intrinsic_value`, `_blend_quality` |
| Reward bar measured in the same units as the candidate | **A** — the candidate got the stats blend while the deck's bar stayed on raw pick rate, biasing toward taking everything. Exactly the asymmetry the note in `_passes` already warned about, with intrinsic value standing in for synergy | `cards._deck_quality_bar` |
| `card_select` waits for enough picks | **A** — "Choose 3 cards to Enchant." arrives with `can_confirm: True` and nothing selected. Confirming sent 0 of 3, the game ignored it, the state froze, and the stuck detector relaunched the game **three times** before it was caught. Third screen where `can_confirm` was wrongly read as "enough selected" | `misc_screens._required_picks` |
| Discard-cost cards ordered last | **A** — `_forces_a_discard` referenced an undefined `_DISCARD_N_RE` and raised `NameError`; both `_play_timing` call sites passed no `hand`, so the branch was unreachable. The rule had never run | `combat._play_timing` |
| Recorder no longer freezes the game | **A** — a `continue` that skipped its sleep turned the shadow recorder into an unthrottled busy-loop; a human player hit a locked shop screen twice, clearing the instant it stopped. Polling is now adaptive, with a Courier-style restock exception | `scripts/shadow_record.py` |
| Absolute quality floor scales with run progress | **A** — a flat 28 applied the same standard on floor 2 with twelve starters as on floor 45. It sat *above* many ordinary commons, so a logged floor-2 offer of Dagger Spray (22), Deadly Poison (23) and Slice (11) was skipped entirely. Now 20.3 early → 28.0 late; calibrated to admit Dagger Spray while still refusing Speedster (18), which was explicitly called out as too weak | `cards._absolute_floor` |
| Card-reward skip memory scoped to the run | **A** — keyed on `(act, floor)` with no reset, so one floor-2 skip auto-skipped floor 2 in *every later run of the session* without opening the screen. Five such cases in one 8-run set, all at low floors that recur every run | `rewards._forget_skips_on_new_run` |
| Beckon's "lose N HP" counted | **A** — `_self_damage_in_hand` matched Infection's "take 3 damage" but not Beckon's "lose 6 HP". Same mechanic, two wordings; the act 1 boss deals them 1–2 per turn and the cost was invisible to incoming damage and to discard ranking. Block cannot answer HP loss, so the bot was defending against something Defend does not stop | `combat._SELF_DAMAGE_RE` |
| Conditional cards recognised as duds | **A** — Bubble Bubble ("If the enemy has Poison, apply 9 Poison") cost 1 energy and did nothing against an unpoisoned enemy | `combat._unmet_condition` |
| Attacks are duds while the enemy is Intangible | **A** — Intangible caps all damage at 1, so a hand of attacks becomes a couple of points. Soul Fysh uses it repeatedly while escalating 7 → 24; those turns belong to Block and setup | `combat._is_dud_this_turn` |
| Deck size counts the deck, not what the enemy added | **A** — a 23-card deck was really 14: nine `Infection` shuffled in by a Wriggler pack sat in the pre-play snapshot. Status cards are combat-scoped; event curses still count | `GameState.true_deck_names` |
| Only needed Block is protected on a discard | **A** — with three block cards against 5 unblocked damage, all three were treated as precious, so a Retain Snakebite (7 poison) was pitched to keep a third redundant Defend | `misc_screens._discard_rank` |
| Raw state logged on combat decisions | **A** — combat logged `hand` as strings like `Strike(1,can_play=True)`, enough to describe a decision and not to reproduce it. A live "why did it keep that Shiv?" was unanswerable. Also the difference between training data and prose | `loop._raw_for_replay` |
| Runs tagged with which Act 1 they rolled | **A** — winning unlocked a second Act 1; both kept in one dataset and tagged, never pooled blindly. Backfilled: the 11- and 20-run sets are `act1_classic`, the partial is `act1_unlocked` | `bot/act_variant.py`, `run_recorder`, `relic_stats` |
| Event offers price added curses and card rarity | **A** — Neow's "Choose 1 of 3 Rare cards… Add 1 Injury" scored 4.00, tied with a vague relic offer: the Injury cost nothing (prose names curses without saying "curse") and three Rare cards were worth +1.0 for containing the word "card". Now 7.50 vs 4.00 | `events._added_junk_cost`, `events._card_offer_bonus` |
| Enemy status *descriptions* are logged | **A** — "Hardened Shell 20" was unreadable from logs; the rule lives in the description | `loop._available_options` |

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

### 1a. ~~`_self_damage_in_hand` misses "lose N HP"~~ — DONE 2026-08-29
Beckon (the Status card Soul Fysh deals out 1-2 per turn) reads:

    type=Status  cost=1  can_play=True
    "At the end of your turn, if this is in your Hand, lose 6 HP."

`_self_damage_in_hand` returns **0** for it. The regex matches Infection's
"take 3 damage" but not "lose 6 HP" -- same mechanic, different wording. So
the 6 HP is invisible to incoming-damage estimation *and* to the discard
ranker's tier 4 ("damages us while held"), which is exactly where it belongs.

The sharp end: **HP loss bypasses Block**, so topping up Defend cannot answer
it. The only fix is getting the card out of hand -- and Beckon is `can_play:
True` at 1 energy, so unlike Infection it can simply be played away. Reported
live as "the bot is not playing the 1 energy to discard it, which is better
than playing a defend". Soul Fysh has killed three runs today.

Fix: widen the regex to "lose N HP", and make the block decision aware that
this portion of expected damage is unblockable.

### 1b. ~~Bubble Bubble played into unpoisoned enemies~~ — DONE 2026-08-29
"If the enemy has Poison, apply 9 Poison." Confirmed dead: with the enemy at
0 poison, `_is_dud_this_turn` returns False and `decide()` plays it anyway,
spending 1 energy on nothing. `_is_dud_this_turn` has no notion of "If the
enemy has X" conditionals -- same shape as the Knife Trap "(Plays 0 Shivs)"
case it already handles.


### 1. Measure the fixed build — IN PROGRESS
Run 20 on the current code and compare to the **A0 baseline: avg floor 13.6,
best 27, act 2 in 4/20**. No further gameplay changes until this lands —
stacking changes makes attribution impossible.

**Status (2026-08-28 23:04):** running, 20 runs, ascension 0 confirmed live.
Bot PID started 21s after the last code change, so the build under test
contains every fix listed above.

**Earlier blocker, resolved:** the set had to be scrapped once because the
floor-48 human run unlocked Ascension 1 and the game defaults the
character-select screen to the highest tier unlocked. Settled by inspecting the
live payload while a human stood on that screen with the control visible: the
API returns only the five characters plus `confirm`/`embark`/`back`, and the
string "ascen" appears nowhere. **The mod does not expose ascension for reading
or setting.** The bot cannot manage it and cannot check before embarking; it
learns the ascension only once a run has started. Mitigations in place:
`loop._check_ascension` logs `unexpected_ascension`, and `relic_stats` records
each run's ascension with `load_history()` filtering to 0 by default, so a
stray run is kept but never averaged into the baseline.

### 2. ~~Hardened Shell / damage caps~~ — DONE 2026-08-28
Resolved the moment the fixed logger met a Skulking Colony. The status text is
**"Skulking Colony cannot lose more than 20 HP each turn"**, and the `amount`
field is the *remaining* allowance, not the printed cap — which is why it was
logged counting down 20 → 17 → 14 → 11 inside one turn.

Holding off on implementing until the text existed was the right call: a
decrementing counter was equally consistent with a one-time pool or something
Block-like, and both imply different play.

### 3. Floor 7–9 elite wall — grade B (measured on `act1_classic` only)
**0% of deaths before floor 7; 45% at floors 7–9** (n=31 runs). Nothing else
is close. Byrdonis (×3), Bygone Effigy (×3), Wriggler packs (×4).

### 4. ~~Wriggler Infection clog~~ — DONE 2026-08-29 (outlet promotion; unmeasured)
Wrigglers add `Infection` ("Unplayable. At the end of your turn, if this is in
your Hand, take 3 damage") every other turn. With 3–4 generators the hand
clogs faster than a starter deck clears it. At the decisive logged turn the
bot held four Infections and one Strike — it did not *choose* not to block, it
had nothing to block with. HP went 68 → 65 → 37 → 32 → 3.

**Correction:** this was briefly marked superseded on the belief that the new
Act 1 replaced the enemy pool entirely. That came from a 10-enemy sample and
was wrong — the pools overlap by 10 enemies and **Wriggler is one of them**, so
this remains a live problem, not a historical one.

Direction: race the generators (17–21 HP each, well in reach) instead of
spreading damage; raise the value of discard/exhaust outlets sharply once
unplayable cards accumulate. The damage itself is already modelled correctly
(`_self_damage_in_hand`) — this is a priority problem, not an arithmetic one.

### 5. ~~Extend the shadow analyzer~~ — DONE 2026-08-28
Measured decisions went 537 → 674; overall agreement **62%**. Per screen:
monster 68%, boss 70%, rest_site 100%, elite 56%, hand_select 56%, map 54%,
event 50%, card_reward 43%, shop 33%. Still unreadable: `card_select` (45) and
`treasure` (6).

Two of the apparent findings were artifacts of the analyzer itself and are
**not** real: `rewards` at 22% is click *order* — the bot claims index 0
repeatedly and works through the whole list, taking everything, just in a
different sequence; and `rest_site` at 60% was trailing records of a spent
campfire (empty options, bot correctly saying `proceed`) being compared against
the human's earlier choice. After fixing, rest sites agree 100%.

**Standing lesson:** three separate measurement artifacts have now been found
in this analyzer (elite 13%→56%, rewards, rest_site). Check a surprising
agreement number against the raw records before believing it.

### 6. Shop policy: removal vs relics — grade C
The bot picked `card_removal` in 4 of 8 shop divergences and potions in 2; the
human bought relics every time (Eternal Feather, Cloak and Dagger, Red Mask,
Dolly's Mirror) and reached floor 48. One run, so a hypothesis only.

### 7. Elite potion behaviour — grade C, do not act yet
The bot opens every elite wanting a potion and holds that view all fight; the
human drank none and reached floor 48. **Contradicted** by an earlier live
observation of the bot dying with three unused potions, and the
aggressive-in-elites rule exists by explicit request. Needs a second human run
or a controlled A/B before touching.

### 8. Map routing — grade C

### 9. ~~`card_select` picks by static score~~ — DONE 2026-08-29
**Confirmed from the log.** At act 1 floor 7, **16/70 HP**, the bot used two
Skill Potions. The same three cards were offered both times:

    [0] Mirage           Gain Block equal to Poison on ALL enemies. Exhaust.
    [1] Cloak and Dagger Gain 6 Block. Add 1 Shiv into your Hand.
    [2] Knife Trap       Play every Shiv in your Exhaust Pile on the enemy.

First pick: Cloak and Dagger — fine. **Second pick: Knife Trap**, which then
appeared in hand annotated by the game itself as **"(Plays 0 Shivs)"**. The
exhaust pile was empty, so the card did literally nothing, and it was discarded
immediately afterwards. At 16 HP the alternative was 6 Block plus a Shiv.

Root cause: `decide_card_select` picks via `_pick_best`, which is
`max(score_card(name))` — a *deck-building* score keyed on name and pick rate
alone. It has no idea what the board looks like. `combat._is_dud_this_turn`
already knows exactly this case (its docstring cites "(Plays 0 Shivs)" from an
earlier live run), but it is only consulted when deciding what to *play*, never
when deciding what to *take*.

Direction: route `card_select` picks through the same contextual check used
for playing, so a card that can accomplish nothing right now cannot be chosen
over one that can. Note the offer is "add into your Hand… free to play this
turn", so this screen is strictly a this-turn decision — deck-building score is
the wrong metric for it entirely.

Also open from the same record: **two potions spent in one turn at 16/70 HP**,
the aggressive-at-low-HP rule firing as designed. Same unresolved question as
priority 7.

`card_select` remains one of two screens the shadow analyzer cannot
reconstruct (45 unreadable decisions), so live observation is the only way it
gets checked — this one was caught by eye, not by tooling.

### 10. ~~Forced discards ignore Retain~~ — DONE 2026-08-29
`_discard_rank` has **no Retain term at all** (confirmed by inspection). Its
tiers cover self-damage, Sly, dead weight, surplus block, attacks, needed block
and lethal — Retain appears nowhere.

That inverts the correct instinct. A card without Retain is discarded at end of
turn regardless, so pitching it to pay a discard cost loses nothing extra. A
Retain card would have carried into next turn *for free* — "Retained cards are
not discarded at the end of turn" — so pitching that one is the only choice
that actually destroys value. Between two otherwise equal cards, the
non-Retain one should always go.

Detectable the same way the retain-prompt fix reads it: live payloads carry
`keywords: [{"name": "Retain", ...}]` (seen on Snakebite).

One interaction to get right rather than bolt on: **Sly wants to be
discarded** — that is its entire payoff — so on a card carrying both, Sly must
still win. The Retain preference belongs below the existing Sly tier, not above
it.

### 11. ~~Thorns is invisible to the bot~~ — DONE 2026-08-29
`grep -i thorn bot/` returns **nothing**. The mechanic is not modelled at all,
yet it is live in the new Act 1: Toadpole (floor 2, 23 HP) carries
`{"name": "Thorns", "amount": 2, "type": "Buff",
  "description": "When hit by an attack, deal 2 damage back."}`

Every attack into a Thorns enemy costs HP, and multi-hit cards pay per hit —
so Shivs and the Silent's whole multi-hit kit are the worst possible way to
kill one, which is precisely the kit the bot builds toward. A five-Shiv turn
into 2 Thorns costs 10 HP the bot never counted.

Reported live as "block before attacking an enemy with thorns". Two parts:
  * count Thorns damage into the turn's expected HP loss, so blocking is
    valued correctly against attacking, and
  * prefer fewer, larger hits over many small ones when Thorns is up —
    the mirror image of the existing Intangible handling, which already
    inverts that preference for the same structural reason.

Parseable from the status description, same as the stun threshold and the
per-turn damage cap.

### 15. Soul Fysh (Act 1 boss) — how to fight it
Observed across 87 logged decisions, all at floor 17 on the `boss` screen.

Its kit, straight from the payloads:

    Attack intents      7, 10, 13, 16, 18, 24        (escalates through the fight)
    StatusCard intents  "give you 1 Status card", "...2 Status cards"
    Self status         Intangible 1 -- "Reduce all damage taken and HP loss
                        to 1. Lasts for 1 turn."
    Applies             Weak 1 to us

Three things make it dangerous, and they interact:

**Intangible turns make our damage worthless.** Every hit lands for 1, and it
caps *HP loss* too, so poison ticks for 1 as well. The bot already knows
Intangible exists (`_effective_damage` collapses to hit count, correctly
preferring three Shivs over one big attack) — but it still *attacks* into it.
Spending three energy to deal 3 damage is close to nothing. The right play is
to treat an Intangible turn as a free turn: block, place Powers, draw, set up
poison to tick later. **Implemented since** -- `_playable_hand` drops cards
whose whole value is damage while an enemy is Intangible, verified live in set
4 run 5 (two Intangible turns, no attack into either). Still open: its
Intangible lands on a fixed 5-round cycle, so the turns can be planned for
rather than only declined.

**Status cards clog the hand**, 1–2 per turn, which is the Wriggler problem
again — and Wriggler is in both Act 1 pools, so the same fix serves both:
value discard/exhaust outlets much higher once unplayable cards accumulate.

**Damage escalates 7 → 24**, so the fight cannot be allowed to drag. That
directly opposes the Intangible answer, which is to stall through it. The
resolution is that stalling is only correct on the turns Intangible is
actually up; the rest of the fight wants maximum pressure.

Practical priority for a fix, once measured: skip attacking on Intangible
turns, and bank the energy into block/setup instead. That single rule converts
the boss's strongest mechanic from a damage sink into free tempo.


## Standing lessons

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
