# STS2 Silent Bot

A rules-based bot that autonomously plays Slay the Spire 2 as the Silent, via the
[STS2MCP](https://github.com/Gennadiyev/STS2MCP) mod's local HTTP API.<img width="2531" height="1336" alt="Animation-ezgif com-gif-maker" src="https://github.com/user-attachments/assets/fb721f6a-bbf2-40af-ac70-a488ddcab259" />





See [KNOWLEDGE.md](KNOWLEDGE.md) for everything learned from real live runs — API
field-name quirks, Silent strategy notes, every bug found and fixed, and how the
engine-freeze recovery works. Read that before touching `strategy/` or `loop.py`.

## How it works

STS2MCP runs an unauthenticated HTTP+JSON API on `localhost:15526` once loaded into
the game. `bot/loop.py` polls `GET /api/v1/singleplayer` for the current state, hands
it to a `strategy/` module keyed by `state_type`, and posts the resulting action to
`POST /api/v1/singleplayer`. No subprocess wiring, no line protocol -- just HTTP.

The hand re-indexes after every card play, so the loop never plans more than one
action ahead of a fresh state fetch.

## Setup

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

   Easiest: double-click one of these in `C:\Claude STS Bot\`:

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

## Project layout

```
bot/
  api_client.py     HTTP wrapper (get_state / post_action)
  game_state.py       defensive accessor layer over the raw state JSON
  loop.py               poll -> dispatch -> act -> log
  main.py                entrypoint
  strategy/
    cards.py             Silent card tags + synergy-aware scoring (data/silent_cards.json)
    combat.py            per-decision combat policy (lethal > survive > value > damage)
    map_nav.py           lookahead path scoring over the full floor graph
    rewards.py           post-combat rewards + card_reward screens
    shop.py, rest.py, potions.py, events.py, relics.py, menu.py, misc_screens.py
tests/                  pytest against real + synthetic fixtures (no game needed)
logs/                   one run_<timestamp>.jsonl per run: state + action + outcome delta
```

## Card knowledge base

`bot/strategy/data/silent_cards.json` was seeded from real STS2 pick-rate data
(sts2.untapped.gg, pulled 2026-08-26), not assumed from the original game --
STS2 rebalanced/renamed a fair amount. Notably, **Sly** (a card discarded from
hand triggers its effect for free instead of being played) is the Silent's
headline mechanic in STS2, so `cards.py` gives sly cards and discard-engine
cards a mutual synergy bonus on top of their base pick-rate.

## Known verification gaps

`shop.py` and `rest.py`, plus `treasure`/`bundle_select`/`crystal_sphere` in
`misc_screens.py`, are still built on defensive field-name guesses -- no live
fixture has cleanly captured one yet. `loop.py` logs the full raw payload for
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
