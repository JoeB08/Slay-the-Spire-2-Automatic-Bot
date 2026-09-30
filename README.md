# STS2 Silent Bot

A rules-based bot that autonomously plays Slay the Spire 2 as the Silent, via the
[STS2MCP](https://github.com/Gennadiyev/STS2MCP) mod's local HTTP API.

This is the public copy of the project: the same bot, without the recorded runs.
It writes nothing at all unless you ask it to -- see **[What it writes](#what-it-writes)**.

See [KNOWLEDGE.md](KNOWLEDGE.md) for everything learned from real live runs — API
field-name quirks, Silent strategy notes, every bug found and fixed, and how the
engine-freeze recovery works. Read that before touching `strategy/` or `loop.py`.

## How this was built

I direct this project and supply the domain side of it: the goals, the Slay the
Spire 2 strategy, and the diagnosis loop. I play the game, watch the bot, and
flag plays that look wrong — most of the rules in `strategy/` exist because a
specific play in a real run looked wrong to me and turned out to be a defect.
The strategy calls are mine too: poison into Lagavulin Matriarch's 12-Block
Plating, capping Prepared at three copies, the damage bar for waking a sleeping
boss, Leg Sweep over two Defends.

**The code itself is written by [Claude](https://claude.com/claude-code)
(Anthropic)** from that direction — implementation, diagnosis and measurement.
Commits carry a `Co-Authored-By: Claude` trailer.

What I care most about here is the evaluation discipline, because the bot is
easy to make *look* better and hard to actually improve:

* Changes are measured over sets of 15–20 runs with 95% confidence intervals,
  and two sets whose intervals overlap have **not** been shown to differ.
* Damage taken **per floor** is tracked alongside average depth. It moved first
  on both occasions a change made things worse, while the average still looked
  fine.
* Changes that measured worse were reverted, including ones that seemed
  obviously right. `CHANGES.md` has a "Decided against" table with the numbers
  that killed each one.
* `scripts/decision_snapshot.py` replays every payload ever logged so a
  refactor can be proven to change no behaviour.

So treat the comments in `strategy/` as evidence rather than gospel: they
record what was observed and measured at the time, and several confident-looking
rules were later removed.


## How it works

STS2MCP runs an unauthenticated HTTP+JSON API on `localhost:15526` once loaded into
the game. `bot/loop.py` polls `GET /api/v1/singleplayer` for the current state, hands
it to a `strategy/` module keyed by `state_type`, and posts the resulting action to
`POST /api/v1/singleplayer`. No subprocess wiring, no line protocol -- just HTTP.

The hand re-indexes after every card play, so the loop never plans more than one
action ahead of a fresh state fetch.

## Setup

Windows only in practice: the launchers are `.bat` and PowerShell, and the
freeze recovery uses `taskkill` and `steam://` URLs to restart a hung game.

1. **Game + mod**: Slay the Spire 2 must be installed with the STS2MCP mod's
   `STS2_MCP.dll` / `STS2_MCP.json` in `<game_install>/mods/`, and mods enabled in
   the game's settings.

   The mod's last **tagged release** (v0.4.0) predates several game-compatibility
   fixes on its `main` branch. If you see `MissingMethodException` /
   `Method not found` errors from the API, rebuild from source instead of using the
   release zip:
   ```powershell
   # from a clone/extract of github.com/Gennadiyev/STS2MCP (needs .NET 9 SDK)
   .\build.ps1 -GameDir "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2"
   ```
   then copy `out\STS2_MCP\STS2_MCP.dll` and `mod_manifest.json` (renamed to
   `STS2_MCP.json`) into the game's `mods\` folder, replacing the old copies. The
   game must be closed while you overwrite the DLL (it locks the file while running).

2. **Python deps**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run it** (game open, mods enabled, at the main menu or mid-run).

   Easiest: double-click one of these in the project folder:

   | File | What it does |
   |---|---|
   | `START BOT.bat` | Launches the game if needed, waits for its API, then starts the bot |
   | `STOP BOT.bat` | Stops the bot (leaves the game running) |
   | `BOT STATUS.bat` | Shows whether the bot is running and whether the game API is up |

   `START BOT` launches Slay the Spire 2 through Steam if it isn't already
   running and waits (up to 3 minutes) for the mod's API to answer before
   starting the bot -- the bot can do nothing until that responds. If the game
   is already up it just uses it. Pass `-NoGame` to the underlying script to
   skip the launch entirely.

   Start-up abandons a run already in progress, so a set measures one build.
   To resume a paused run instead, run `scripts\bot_control.ps1 start -KeepRun`
   from the game's main menu -- the bot then picks "continue".

   `START BOT` always stops an existing bot before starting: two bots against the
   same game fight over every screen and issue conflicting actions, which looks
   exactly like the bot ignoring your code changes. `BOT STATUS` warns loudly if
   it ever finds more than one.

   Or from a terminal:
   ```bash
   python -m bot.main
   ```
   It drives its own run from the main menu (character select -> embark) and keeps
   playing indefinitely, including starting a new run after a win/loss, unless
   `--max-actions N` is passed.

## What it writes

Nothing, unless you ask it to. A fresh clone plays the game and leaves no files
behind.

Recording is how this project measures itself, and it is a lot of output per run
-- a decision log of every state and action (~3 MB), a summary line, a readable
story, an entry in the relic history. Turn it on with either:

* a file named `RECORD_RUNS` beside the `bot/` package -- what the development
  copy has, so it records by default; or
* `STS2_RECORD=1` in the environment, for one process.

`STS2_RECORD=0` forces it off again. The switch only decides whether what
happened gets written down: nothing in `strategy/` or the decision pipeline
reads it, so the bot plays an identical game either way. See
`bot/recording.py`.

With it on:

| Path | What lands there |
|---|---|
| `logs/run_<timestamp>.jsonl` | one JSON line per decision, per bot process |
| `logs/runs.jsonl` | one summary line per finished run |
| `logs/stories/` | a readable account of each run, and an `INDEX.md` |
| `logs/deck_memory.json` | the deck cache, so a restart mid-run isn't blind |
| `stats/relic_history.jsonl` | each run's relics, kept across log wipes |
| `stats/relaunch_log.txt` | every time freeze recovery restarted the game |

The decision logs are the project's only real regression test -- every archived
payload gets replayed to prove a refactor changed nothing -- so they are never
deleted. They are also about 97% air:

```bash
python scripts/compress_logs.py --dry-run   # what it would save
python scripts/compress_logs.py             # gzip finished logs in place
```

Every reader goes through `bot/log_io.py` and handles both forms, so a packed
archive behaves exactly like an unpacked one, right down to the payload
ordering `decision_snapshot.py` numbers its baseline by. `--decompress` puts it
all back.

## Reviewing runs

Every finished run appends one summary line to `logs/runs.jsonl` — outcome,
act/floor reached, HP low-water mark, damage taken, gold, final deck and
relics, what was in the fight when it died, and counts of every screen and
action. This is the right unit for judging the bot: the per-decision logs are
written per bot *process*, and one process can play several runs while one run
can span processes after a restart.

To review:

```bash
python scripts/analyze_runs.py --last 10
```

It reports floor/act reached, what keeps killing the bot, how often the
loop-recovery safety nets fired, where decisions are being spent, and how many
starter cards are still sitting in final decks (a direct read on whether
removals are keeping up).

The rest of `scripts/`:

| Script | What it answers |
|---|---|
| `audit_decisions.py` | Scans a decision log for the defect classes this project keeps finding — wasted energy, zero-value plays, missed lethals, draws into a full hand. Candidates, not proof: replay one before believing it. **Run it after every batch of changes** — it has caught regressions that both review and the test suite missed. |
| `decision_snapshot.py` | Replays every raw payload ever logged through the current code and records the decision. `record` before a refactor, `compare` after: a pure cleanup reports zero differences. This is what makes changing `combat.py` safe. |
| `card_rewards_audit.py` | Counts card rewards abandoned unopened. Should be **zero** — it was 46% before the skip-memory fix, which cost ~2.5 cards per run. |
| `compress_logs.py` | Gzips finished decision logs in place (396 MB -> 13 MB here), verifying each one by hash before deleting the original. `--decompress` reverses it. |
| `set_history.py` | Average depth per run set, with a 95% margin. The margin is the point: two sets whose intervals overlap have not been shown to differ. |
| `relic_table.py` | Act 1 relics vs how far the run got, separating the Neow choice (comparable) from every relic held (not). |
| `update_report.py` | Refreshes the live section of a session report from `logs/runs.jsonl`. |
| `shadow_record.py` / `shadow_analyze.py` | Record a human run and compare it against what the bot would have done. |

**Read the margin, not the average.** Run depth varies enormously — one build
produced floors 7 through 43 — so a 20-run set is worth roughly ±3 floors.
Watch **damage taken per floor** too: it moved first on both occasions a
change made things worse, while the average still looked plausible.

## Project layout

```
bot/
  api_client.py     HTTP wrapper (get_state / post_action)
  game_state.py       defensive accessor layer over the raw state JSON
  loop.py               poll -> dispatch -> act -> log, plus freeze recovery
  recovery.py             stuck/cycle detection and kill-and-relaunch
  run_recorder.py           per-run summary lines for logs/runs.jsonl
  deck_memory.py              what the deck actually holds right now
  recording.py                  whether any of this gets written down
  log_io.py                       reading decision logs, packed or not
  act_variant.py                which of the two Act 1s this run rolled
  relic_stats.py                  relic history across sets (never wiped)
  explore.py                        relic-exploration mode
  main.py                             entrypoint
  strategy/
    combat.py            the per-turn decision pipeline -- see below
    cards.py             Silent card tags + synergy-aware scoring (data/silent_cards.json)
    boss_intel.py        remembers the act's boss from the map, and what it implies
    map_nav.py           lookahead path scoring over the full floor graph
    rewards.py           post-combat rewards + card_reward screens
    misc_screens.py      card_select, hand_select, treasure, crystal_sphere
    shop.py, rest.py, potions.py, events.py, relics.py, menu.py
tests/                  pytest against real + synthetic fixtures (no game needed)
scripts/                analysis and tooling -- see "Reviewing runs"
logs/                   one run_<timestamp>.jsonl per bot process: state + action + outcome
stats/archive/          completed run sets, each with a README recording what it measured
```

### How `combat.py` decides a turn

`decide()` is a pipeline of named steps, tried in order, each either returning
an action or falling through to the next:

```
potions -> death clock -> thorns -> fan of knives -> chain kill -> lethal -> duplication
-> capped turn -> kill the attacker -> stun threshold -> powers -> sequencing -> free damage
-> purge held status -> mitigation -> survive -> value -> damage
-> leftover block -> final leftover scan -> end turn
```

**That order is the thing to understand before changing anything.** Nearly
every bug this project has found was an earlier step committing the turn
before a later one was consulted — Choke losing to the free-damage step,
Flechettes to the block step, a Sly card being paid for instead of discarded.
Shared per-turn state (`_turn_context`) is computed once up front and passed
to each step.


## Card knowledge base

`bot/strategy/data/silent_cards.json` was seeded from real STS2 pick-rate data
(sts2.untapped.gg, pulled 2026-08-26), not assumed from the original game --
STS2 rebalanced/renamed a fair amount. Notably, **Sly** (a card discarded from
hand triggers its effect for free instead of being played) is the Silent's
headline mechanic in STS2, so `cards.py` gives sly cards and discard-engine
cards a mutual synergy bonus on top of their base pick-rate.

## Known verification gaps

`shop.py` and `rest.py`, plus `treasure`/`bundle_select` in
`misc_screens.py`, are still built on defensive field-name guesses -- no live
fixture has cleanly captured one yet.

`crystal_sphere` (the Crystal Sphere divination minigame) **has** been captured
since -- see `tests/fixtures/crystal_sphere.json`. It cost a floor-31 run to
find: the handler sent `crystal_sphere_proceed` unconditionally, the game
rejects that while `can_proceed` is false, and the freeze recovery relaunched
the game rather than leaving the screen. The reveal-cell action name is still
unverified, so the handler probes candidates and remembers whichever is
accepted. `loop.py` logs the full raw payload for
these automatically (`screen_raw`) so the next real occurrence is fixable
straight from the log. See [KNOWLEDGE.md](KNOWLEDGE.md) for the full list of
gaps closed so far and how each one was actually found.

Each degrades to a safe, loggable default rather than crashing the run --
check `logs/run_*.jsonl` after a real session for `"result": "error"` lines or
obviously-wrong actions on these screens, and correct the field names there
first.

## Future direction

The per-decision logs capture (state summary, action, outcome delta) rather
than free text, so if a self-improving version gets built on top of this
rules-based one later, these logs double as its training traces.

## License

MIT. See the LICENSE file.

Slay the Spire 2 is not mine; this project is unaffiliated with its developers
and with the STS2MCP mod. The card data in `silent_cards.json` was seeded from
public pick-rate data at sts2.untapped.gg.
