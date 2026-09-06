# STS2 Silent Bot — Knowledge Base

> Changes needed / in progress / done live in **`CHANGES.md`**, kept up to
> date with each edit. This file is for how the game and API behave.

> **In-flight work:** see [CURRENT_WORK.md](CURRENT_WORK.md) for what's running
> right now and the outstanding user-reported issues not yet acted on.

Everything learned about the game, the API, and this bot's own behavior that
isn't obvious from reading the code once. Update this file whenever a live
run turns up something new — that's how most of this was found.

## The game itself

- **Slay the Spire 2**, Early Access (released 2026-03-05), Godot engine.
  Steam app id `2868840`. Install: `C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2`.
- Characters must be **unlocked** via the Timeline meta-progression system
  before they're selectable — a fresh profile only has Ironclad. Silent was
  manually unlocked on this machine's profile early on.
- **Sly** is the Silent's headline new mechanic: a Sly card discarded from
  hand (not played) triggers its effect for free. Community sources describe
  it as the Silent's primary win condition in STS2 — build around discard
  engines (Tools of the Trade, Acrobatics, Prepared, Hidden Daggers, Storm of
  Steel...) feeding Sly cards (Abrasive, Haze, Tactician, Untouchable,
  Ricochet, Reflex, Flick-Flack...). `bot/strategy/cards.py` scores this
  synergy explicitly.
- Card data (`bot/strategy/data/silent_cards.json`) was seeded from real
  empirical pick-rate stats (sts2.untapped.gg, pulled 2026-08-26), not
  assumed from the original game — STS2 rebalanced/renamed a fair amount.
  Some cards are multiplayer-only (buff "another player") and are penalized
  hard in scoring since the bot only plays solo.
- The full floor map graph (`map.nodes`) is handed over up front, not just
  the next step — `map_nav.py` does real lookahead scoring toward the boss
  instead of a greedy next-node pick.

## The STS2MCP mod / API

- Repo: github.com/Gennadiyev/STS2MCP. Runs an **unauthenticated local HTTP
  API on `localhost:15526`** once loaded — `GET`/`POST /api/v1/singleplayer`.
  No subprocess wiring, no line protocol, just plain JSON.
- **The last tagged release (v0.4.0) is stale.** The game is Early Access and
  updates its internal API fast; the mod's GitHub has a recurring stream of
  "restore compatibility" fixes (issues #110/#114–#123) that never got cut
  into a new release. **Build from source** instead (`.NET 9 SDK` +
  `build.ps1 -GameDir "<path>"`) — see README for the exact steps. If the API
  starts throwing `MissingMethodException`/`Method not found` again after a
  game update, that's what's happening again: pull `main`, rebuild, reinstall.
- The `wiki`/`compendium` endpoints only return **discovered** content — on a
  fresh profile they're empty, which is why the card database is a static
  seeded file rather than pulled live.
- **Field names are not always what the doc summary says.** Every screen
  below was corrected against a real captured payload (in `tests/fixtures/`)
  after the assumed shape caused a live infinite loop:
  - `select_card_reward` needs `card_index`, not `index`.
  - `hand_select`'s offered cards/current selection live under
    `hand_select.cards` / `hand_select.selected_cards` / `hand_select.can_confirm`
    — **not** the main `player.hand`. Loop until `can_confirm` is true, then
    `combat_confirm_selection`.
  - `card_select` (Transform/Duplicate/etc.) payload is under `card_select`,
    not top-level `cards`/`options`. Selecting a card triggers a **preview**
    (`card_select.preview_showing: true`) that needs its own
    `confirm_selection` before it's final — don't just re-select.
  - `card_select.screen_type == "transform"` should target your **worst**
    card (a spare Strike/Defend) to sacrifice; other screen_types should
    target your best.
  - `game_over`'s options are nested under `game_over.options`, not the
    top-level `options` the menu screens use.
  - The `timeline` meta-progression screen needs `obtained_unrevealed_count`
    checked explicitly (`advance` while >0, then `back`) — this is also a
    known upstream pain point, STS2MCP issues #92/#93.
  - `character_select` does **not** expose which character is currently
    highlighted. `confirm`/`embark` being enabled just means *some* character
    is selected — the game can default/retain a different one (confirmed
    live: it retained Regent instead of Silent after a run ended). Always
    explicitly re-select Silent on every fresh visit to the screen before
    ever confirming; never trust "confirm enabled" as a proxy.
  - `shop` and `bundle_select` are now **verified** against real payloads
    (fixtures `shop.json`, `bundle_select.json`) -- both had guessed field
    names that silently failed. Rest-site options, treasure, and
    crystal_sphere are still **unverified** — no live fixture ever captured
    one cleanly. `loop.py` logs the full raw payload for these screens every
    time one comes up (`screen_raw` field), specifically so the next real
    occurrence is fixable from the log alone.
- **Screen transitions are not instant.** A successful action can leave the
  next poll or two still showing the old screen (confirmed live: a
  `game_over` -> `menu_select main_menu` took ~2 real seconds to actually
  land on the menu, even though every intermediate poll reported the action
  succeeded). Don't treat "same state_type on the next poll" as a bug by
  itself — only real trouble past ~30s (see Recovery below).
- **`post_action` can raise a raw network exception**, not just the mod's own
  `{"status":"error"}` (`ApiError`) — a live run hit an uncaught
  `requests.exceptions.ReadTimeout` right after a recovery relaunch while the
  game was still settling, which crashed the whole process before this was
  caught. `loop.py` catches `(ApiError, requests.exceptions.RequestException)`.

## Genuine engine freezes (not just slow transitions)

Confirmed live: the game can fully stop advancing mid-combat (identical
`GET` response for many minutes) with no in-game recovery available through
the API — **there is no flee/abandon/pause-menu action reachable from
combat**, and the mod exposes no keyboard/mouse injection at all (grepped
the mod's own source to confirm; STS2MCP is HTTP-only, unlike the original
game's CommunicationMod which had `KEY`/`CLICK`).

Recovery (`bot/recovery.py` + `BotLoop`): fingerprint every poll; if it's
byte-identical for >30s of real wall-clock time, kill `SlayTheSpire2.exe` and
relaunch via `steam://rungameid/2868840` (not the exe directly, so Steam's
own DRM/overlay handling stays intact), then use the main menu's `continue`
to resume. STS2 autosaves at least per-encounter — a relaunch mid-fight
resumed at the **start** of that same fight (full enemy HP), not the exact
turn, so expect to lose in-progress-fight state but not the whole run. If a
resumed run freezes again right away, the bot escalates to `abandon_run`
after `ABANDON_AFTER_N_RECOVERIES` (2) so it can't loop kill→resume→freeze
forever. Caps out and raises after `MAX_CONSECUTIVE_RECOVERIES` (3).

## Combat tuning notes

- **Block is need-based, not automatic — but "need" is not "would I survive".**
  Block is waived only for genuine *chip* damage (`max(3, 5% of max HP)`,
  i.e. ~3 at 70 max HP) while above the safety floor — that's the take-1-to-
  deal-6 trade. Anything bigger gets blocked even at full HP, because HP is a
  run-long resource in StS, not a per-fight one. An earlier version keyed
  purely off "would this drop me below 25% HP?" and consequently ate an
  11-damage hit at 44/70 while holding a Defend; don't reintroduce that.
  Two related traps if you touch this: (a) pure block cards are excluded from
  step 3 (value) when blocking isn't needed, since they'd otherwise win on
  generic archetype score alone (decks are full of Defends) and silently undo
  the logic; (b) step 5 exists so the bot never *ends* a turn holding a block
  card with spare energy and real incoming damage — unspent energy is simply
  lost, so blocking is free value at that point.
- When block **is** needed, the smallest card that actually closes the gap
  is played, not reflexively the biggest one on hand.
- **Killing an attacker beats blocking it.** A threat we can finish *this
  turn* -- even across several cards -- removes 100% of its damage
  permanently, while block is spent and gone. The single-card lethal check
  misses this (two Strikes into a 10 HP attacker), which had the bot blocking
  hits it could have deleted. `_reachable_damage` sums affordable attacks
  greedily by damage-per-energy to make the kill/no-kill call, and the step
  only commits when the *other* enemies' remaining damage won't kill us
  anyway.
- **Kill with the smallest sufficient hit.** The lethal picker originally took
  the *biggest* lethal card, so an 11-damage Backstab got dumped on a 5 HP
  enemy while the 10 HP one beside it survived a 6-damage Strike. It now
  minimises overkill (then cost, then avoids Sly), which keeps heavy hitters
  free for targets that actually need them. Same "smallest sufficient"
  principle as the block chooser.
- **Poison counts as guaranteed future damage** for lethal purposes (it kills
  the enemy before their own next turn regardless of what we do) — an
  enemy's *effective* HP is `hp - current_poison_stack`. An enemy poison
  alone will finish off is dropped from targeting and from the incoming-
  damage calculation entirely (their queued attack won't resolve either).
- **Scaling buffs go first.** Dexterity raises Block gained and Strength
  raises Attack damage, so a buff played *after* the Defends/Strikes it was
  meant to pump is wasted for the turn. `combat.py` plays Dexterity/Strength
  gains ahead of block, attacks, and even free 0-cost damage -- but still
  behind the lethal check (just win if you can), and it's skipped when we're
  under real pressure and can't afford both the buff and enough block.
  Detection matches "Gain N Dexterity/Strength" only, so Wraith Form
  ("...lose 1 Dexterity") and Malaise ("Enemy loses X Strength") don't
  qualify. Note the safety check deliberately does *not* require a single
  card to fully cover the incoming hit -- when nothing covers it we're taking
  damage anyway, and the Dexterity improves every Defend that follows.
- **Weak must target an attacker.** Weak only reduces *Attack* damage, so
  applying it to a non-attacking enemy does nothing at all. `_choose_target`
  sends Weak-applying cards at the hardest-hitting attacker, falling back to
  lowest-HP only if nobody is attacking. **Route every target through
  `_choose_target`** -- a first pass wired it into only 3 of 8 target sites,
  and Weak kept landing on idle enemies because Weak-applying *skills*
  (Leg Sweep, Malaise) deal no damage and so route through the *value* step,
  not the attack step. Also note X-cost cards spell the amount as a literal
  "X" ("Apply X Weak"), so digits-only patterns silently miss them.
- **Count our own defensive statuses before blocking.** Block we'll get for
  free (Plating / Metallicize-style "gain N Block at end of turn") is added to
  current block, and Intangible collapses incoming damage to 1 per hit. Not
  modelling these meant overblocking -- spending cards and energy covering
  damage that was already handled or couldn't land. Detection is by status
  name *and* effect text, so unfamiliar equivalents still register.
- **Block values must include Dexterity.** `_effective_block` adds the
  player's Dexterity to a card's printed Block; ignoring it made the bot
  mis-rank block cards and under-cover incoming hits.
- **Reserve energy for block, or chip damage compounds.** Step 4 (damage)
  used to spend every last energy on attacks, so step 5 (leftover block)
  never actually got any -- small hits then piled up turn after turn. Step 4
  now holds back the cost of an "efficient" block card whenever there's
  unblocked damage. Efficient means the hit consumes at least
  `BLOCK_EFFICIENCY_RATIO` (0.5) of the card's block, so a 5-block Defend is
  played against 3+ damage but not wasted on 1 -- which keeps the
  take-1-to-deal-6 trade intact while stopping the bleed.
- **Long elite fights: race, don't turtle.** Damage taken scales with how many
  turns a fight lasts, so blocking 4 a turn for ten turns costs more HP than
  eating 12 across three while killing sooner. `_should_race` widens the chip
  threshold (x`RACE_CHIP_MULTIPLIER`) when it's an elite/boss **and** the
  fight has more than `RACE_MIN_TURNS` of grinding left **and** HP has real
  headroom above the safety floor. It never disables survival: the
  safety-floor and about-to-die checks still bind, so a big hit is blocked
  regardless. The bot was previously chipped to death grinding elites down --
  most visibly against the one that ended four runs in a single 10-run set.
- **A run already in progress at startup is abandoned, not resumed.** A
  half-played run isn't a measurement of the current build (older code, played
  by hand, interrupted partway), so counting it pollutes results. There is no
  "open the menu" action in the API, so the only route to the main menu --
  where `abandon_run` lives -- is to relaunch the game; the loop then reuses
  the same `prefer_abandon` path the freeze-recovery uses. The discarded run
  is never recorded because `RunRecorder` only writes at `game_over`, which it
  never reaches. Deck memory is cleared at the same time, since that deck
  belongs to the run being thrown away.
- **Confirm `end_turn` against a fresh poll.** Effects can land *after* the
  state is read: discarding a Sly card (Tactician: "Sly. Gain 1 Energy")
  plays it for free a beat later, so a turn ended on the pre-effect snapshot
  threw away both the energy and the cards it could have paid for. Since
  ending a turn is irreversible, the loop re-polls and re-decides once before
  committing, and only ends the turn if the answer is still `end_turn`.
  This also explains sightings of "ended with shivs / energy in hand" that
  left no trace in the logs -- the logged snapshot was taken before the
  effect resolved, so it looked correct after the fact.
- **Never end a turn with energy and a playable card.** Energy doesn't carry
  over, so a final scan (step 6) plays the best remaining playable card
  before `end_turn`. This exists because every earlier step has a bar a card
  can fail -- the value step needs a minimum synergy score, the block steps
  need incoming damage -- which stranded playable cards in hand. The scan
  deprioritizes block that would just decay when nothing is attacking. If you
  add a new priority step with its own threshold, this backstop is what keeps
  that threshold from silently wasting energy.
- **Never pay energy for a Sly card.** Sly means "if discarded from your Hand
  before the end of your turn, play it for free", so spending energy on one
  throws the keyword away. `cards.is_sly` detects it (keywords list, a "Sly."
  description prefix, or the static card data), combat deprioritises Sly cards
  in every play-selection step, and the discard chooser treats them as the
  *best* thing to pitch. Exhaust prompts are different -- Sly gets no trigger
  there, so exhaust falls back to "lose the worst card".
- **Removal is not "inverse pick rate".** `cards.removal_priority` ranks what
  to cut: curses > status > starter Strike/Defend (more copies = better cut) >
  weak real cards > good cards. Ranking by inverse `score_card` was actively
  wrong -- starters have no pick rate and default to ~30, so they *out-scored*
  a genuinely weak card like Slice (5) and the bot removed Slice while keeping
  the Strike. Neutralize/Survivor rank below Strike/Defend since they still
  pull weight (Weak application, a discard outlet).
- **A "removed cards come back Upgraded" relic inverts removal.** With that
  relic the removal screen is an *upgrade* choice, not a thinning one, so the
  usual prime cuts become the worst picks -- upgrading a starter Strike is
  nearly worthless and curses/status can't be upgraded usefully. The bot
  detects the relic from its text and switches to `cards.pick_upgrade_target`,
  which drops junk and starters then takes the **median-scoring** card: good
  enough to still be in the deck at the end of the run, but not one the deck
  is already winning with.
- **Intangible enemies invert card choice.** Intangible caps every hit at 1,
  so size stops mattering and only hit *count* does: three shivs land 3 where
  an 11-damage Backstab lands 1. `_effective_damage` switches to `_hit_count`
  against such enemies, which naturally makes the bot dump cheap multi-hit
  cards and hold big attacks until it wears off.
- **Order-dependent cards.** Flechettes ("damage for each Skill in your Hand")
  shrinks as skills leave hand, so it plays *early*; Finisher, Precise Cut,
  Memento Mori and Pinpoint all grow as the turn progresses, so they play
  *last*. `_play_timing` encodes this and `_contextual_damage` gives the real
  number for hand-scaled cards -- the flat regex read Flechettes as a
  5-damage card, badly under-estimating it for lethal.
- **Sly enablers.** A discard card that would trigger a Sly card in hand
  (Survivor next to a Sly attack) is block *plus* a free attack, so it's
  preferred over a plain Defend of equal block. Ranking block purely by block
  value misses the Silent's core engine.
- **Enemy Block changes who to hit.** `_damage_to_kill` = enemy Block +
  post-poison HP, and targeting uses it instead of raw HP -- a 3 HP enemy
  behind 30 Block soaked every attack while an unblocked 12 HP enemy stood
  next to it. Poison is the exception: it bypasses Block, so `_kills_enemy`
  checks poison against post-poison HP *without* Block, and attack damage
  against Block + HP.
- **`can_play` beats our own cost arithmetic.** Costs change mid-turn (Pounce
  makes the next Skill free, Bullet Time frees the hand, Master Planner), so
  the printed cost stops reflecting what we'd pay. `_playable_hand` trusts
  `can_play` when the field is present and only falls back to cost maths when
  it's absent. Relatedly the final leftover-energy scan has **no** `energy > 0`
  gate: a card reduced to 0 cost is still playable at 0 energy.
- **Weaken before blocking.** Weak (and enemy Strength reduction) shrink the
  incoming hit, so applying them *after* block means the block was sized
  against the bigger number and part of it is wasted. Mitigation is its own
  step ahead of the block decision. This mattered because Weak-applying
  *skills* (Leg Sweep, Piercing Wail) deal no damage and so used to fall into
  the value step, which runs after block.
- **Rest sites are judged on HP actually gained, not HP percentage.** Healing
  is capped by the HP we're missing, so a "heal 30% of max" rest at 66/70
  returns 4 -- strictly worse than a permanent card upgrade. `rest.py`
  computes the real gain (reading an explicit amount or percentage from the
  option text when present) and only rests when it clears a worthwhile
  threshold, or when we're badly hurt enough that surviving the next fight
  outranks a permanent improvement.
- **HP can't exceed max, so heals can be wasted.** A heal is only spent when
  the missing HP soaks up at least half of it -- a live run burned a healing
  potion at full health for zero benefit. The overflow-spending branch carries
  the same guard: overflowing a potion loses nothing, but using it at full HP
  is no better.
- **Regen is a long-fight card.** Its value is roughly (turns remaining x
  amount), so it's used early in elites/bosses or fights with a lot of enemy
  HP left, and held in short trash fights where most ticks would never
  happen. It's excluded from the emergency healer list -- a trickle is no
  answer to being about to die.
- **Guaranteed potion drops make potions renewable.** With a relic granting N
  potions per combat, anything still held when the next batch lands is lost to
  overflow, so the bot spends down to leave room -- burning the *least*
  valuable potion and never a reserve-tier one (a revive outvalues any
  refill). Detected from relic text, not a name list.
- **Ethereal inverts the junk instinct.** An Ethereal card exhausts itself if
  held to end of turn, so *holding* an Ethereal curse deletes it for the
  combat while discarding only recycles it back into the deck. Ethereal dead
  weight is therefore demoted from "pitch first" to an ordinary discard
  candidate, and a forced Exhaust effect prefers a non-Ethereal target since
  an Ethereal one would exhaust anyway.
- **Discard priority** (`misc_screens._discard_rank`), most-discardable first:
  Sly cards (free play) > dead weight (Curse/Status/unplayable) > cards we
  can't afford this turn > ordinary non-attacks > affordable attacks > an
  attack that is currently lethal (never pitched). Attacks are protected
  because they convert to damage *this turn*; scoring alone was pitching them.
- **Leaders: killing them can end the fight.** Some encounters resolve the
  moment one enemy dies, so that kill outranks any amount of blocking -- the
  "would the other enemies kill me?" guard on multi-card kills is skipped for
  them. **The API exposes no leader/minion flag** (enemies carry only
  entity_id, name, hp, block, status, intents), so `_is_likely_leader` is a
  heuristic over the name plus "does it summon things". This is the most
  likely part of combat.py to be wrong on an unfamiliar encounter; being wrong
  only relaxes a safety guard on a kill we can already reach.
- **Map routing weighs gold.** Gold is worth nothing at the end of a run, so
  shops scale from near-worthless when broke (<75g) up to a strong detour when
  rich (300g+). Survival still wins: below 35% HP a rest site outranks any
  shop.
- **"Choose cards" prompts point in two directions.** Screens whose prompt
  mentions discard/replace/exchange/remove/transform *throw away* the chosen
  card (Gambling Chip's start-of-combat replace, removal services), so those
  pick our **worst** card; add-to-deck/duplicate/upgrade prompts pick our
  best. A blanket "pick best" handed away good cards on the replace screens.
- **Poison counts toward lethal.** Poison a card *applies* is added to its
  effective damage (unscaled -- Vulnerable boosts attacks, not poison), and a
  pure-poison card like Deadly Poison counts as an attack option. Poison ticks
  at the start of the enemy's turn, so a stack exceeding its HP kills it
  before it acts; no further cards need to be spent on it.
- **One-turn buff potions must be held until they pay off.** Flex (temporary
  Strength) and Speed (temporary Dexterity) expire at end of turn, so the
  "spend freely in elites" branches used to burn them on turn 1 for nothing.
  They're now excluded from the generic branches and used only when the buff
  changes *this turn's* outcome: Strength that turns a survivable enemy into a
  dead one, or Dexterity that turns an uncoverable hit into a covered one.
- **Potions are spent, not hoarded.** An unspent potion is worth nothing if
  the run ends. Elites/bosses get everything (offense, defense, utility); low
  HP in an ordinary fight reaches for defense/healing only -- deliberately
  *not* utility, since drawing cards is no answer to being low and the potion
  is better saved for an elite. Emergencies (about to die / critically low)
  override everything. When the belt is full and a clearly better potion is
  offered, `rewards.py` discards the worst held one to make room
  (`discard_potion` works outside combat -- verified in the mod source).
- **Events are HP-aware.** HP costs are priced against *current* HP, escalate
  once hurt, and anything that would leave us at/below ~20% max HP is
  rejected outright in favour of a proceed/leave option. Caveat: the
  heuristic reads HP costs but not reward *amounts*, so it can't trade HP for
  a big gold payout -- it errs toward safety.
- **Curses sitting in hand that deal direct damage** (e.g. a "take N damage
  at end of turn" Unplayable curse) count toward incoming damage for the
  block decision, same as an enemy attack — checked against the *full* hand,
  not just the playable subset, since these are typically Unplayable.

## Findings from the first 10-run sample (all Act 1 deaths, avg floor 12.8)

- **Card removal could never be bought.** `full_deck_names()` reads the
  hand/draw/discard/exhaust piles, and those are **only populated during
  combat** -- outside combat the deck reads as empty. `shop.py` gated removal
  on "do I have Strikes/Defends worth cutting?", so the gate always failed:
  removal was offered 21 times, affordable 6+, bought **0**. Same root
  assumption bit `run_recorder` (deck recorded as size 0). **Any code that
  inspects the deck outside combat needs a cached mid-run snapshot.**
- **Campfires never upgraded**: 54 rest decisions, 22 Rests, **0 Smiths**. The
  "is this heal worthwhile?" threshold (~8 HP) is essentially always met given
  ~98 damage taken per run, so Rest always won.
- **Takes nearly every card**: 43 taken vs 2 skipped, giving 24.4-card decks
  still holding 9.6 starters each, plus statuses (41 Infection, 12 Wound).
- **Powers were played late**: 50 of 73 turns holding a playable Power played
  something else first (Backstab/Ricochet ahead of Accuracy). Fixed -- the
  early-play step now covers Power-type cards, not just Dexterity/Strength.
- Wriggler appeared in the final fight of 5 of 10 runs (avg floor 10).

## Upgrade data

`bot/strategy/data/silent_upgrades.json` holds per-card upgrade values,
imported from the **game itself** by `scripts/import_upgrade_data.py`. The
mod's `/api/v1/wiki` endpoint returns both variants of every *discovered*
card (`base` and `upgraded`), so the delta is real data rather than a guess.

Re-run the importer after a game update, or once more cards are discovered --
it only sees the active profile's discoveries (80 of 95 Silent cards at the
time of writing; the rest fall back to `DEFAULT_UPGRADE_GAIN`).

Why it matters: cards upgrade *very* unevenly. Tools of the Trade and
Shadowmeld drop 1 → 0 cost, Adrenaline and Tactician gain a whole extra
Energy, while a Strike gains 3 damage. Before this existed there was no basis
for choosing an upgrade target at all, and `pick_upgrade_target` used
"median-scoring card" as a stand-in.

Gotchas the importer has to handle, all found by inspecting the output:
- **Energy is drawn as icon tags**, not digits (`[silent_energy_icon.png]`),
  so Adrenaline and Tactician scored 0 until icons were counted.
- **Cost reductions don't change the text at all** -- eight cards had
  identical base/upgraded descriptions and differed only in `cost`.
- Many upgrades are ordinary number bumps no named pattern covers ("3 times"
  → "4 times", "Retain up to 1" → "2"), so there's a generic positional
  numeric diff as a catch-all. Only 1 of 80 cards (Burst) still scores 0.

## Deck-building policy (rewritten after the 10-run sample)

- **`deck_memory` is mandatory outside combat.** The card piles are only
  populated during combat, so `gs.full_deck_names()` returns `[]` at shops,
  rest sites and card rewards. `bot/deck_memory.py` snapshots the deck during
  combat and serves it everywhere else; it clears itself when the floor goes
  backwards (new run). **Never call `full_deck_names()` directly from a
  non-combat screen** -- that's what silently disabled card removal for 10
  straight runs.
- **Skip policy is quality relative to the current deck.** A candidate must
  beat the *median intrinsic quality* of the real cards we already hold.
  Two traps, both hit while building this:
  1. The bar must use **intrinsic** quality, not `score_card`: synergy is
     awarded per copy of a shared tag, so five starter Strikes each collect
     the `damage` bonus and score ~54, pushing the bar above genuinely good
     cards and skipping everything from a starting deck.
  2. The take/skip gate must compare **like with like**. Crediting the
     candidate with synergy while the bar has none biases toward taking -- it
     let Slice (pick rate 5) clear a starter deck's bar. Synergy now decides
     *which* card to take; intrinsic quality decides *whether* to take one.
  Starters count as `STARTER_QUALITY` (low) since they're what removals
  delete. Junk is excluded from the bar so a clogged deck doesn't lower
  standards exactly when it's worst.
- **Archetype commitment ramps with the run.** Act 1 is a soft lean
  (`SOFT_COMMIT_BONUS`, never a penalty) so early picks can reveal the engine;
  Act 2+ is a hard commit (bonus + penalty for off-plan cards). Universally
  good cards (energy/draw/block/power) are exempt from the penalty.
- **Grand Finale is conditional, not a free build-around.** It needs an empty
  draw pile, and it has the *worst* pick rate in the whole Silent pool (7%)
  with negative act/run winrate deltas -- because in a normal-sized deck it's
  a dead card. It's taken only into a deck already small enough to plausibly
  empty (`GRAND_FINALE_MAX_DECK`); above that it's judged on its own (poor)
  merit like any other card. Once it *is* in the deck the goal flips: stop
  taking cards entirely and let removals finish the thinning.
- **Rest sites upgrade by default.** Heal only at/below `HEAL_PRIORITY_HP_FRACTION`
  (50%) when the heal is worthwhile, else Smith. The old ordering rested
  whenever the heal cleared a small absolute threshold, which -- at ~98 damage
  taken per run -- meant 22 Rests and **0 upgrades** across 10 runs.

## Bugs found & fixed so far (all have regression tests)

| Bug | Symptom live | Fix |
|---|---|---|
| Shiv/discard damage ignored | Leading Strike ranked below plain Strike | `combat.py` sums shivs generated into total damage |
| Vulnerable not sequenced | Plain attacks played before Vulnerable-setup ones | Vulnerable-applying attacks on an undebuffed target go first |
| Free damage left unplayed | Turn ended holding 0-cost Shivs | Dedicated step plays any playable 0-cost attack before anything else |
| `hand_select` infinite loop | Kept re-selecting, never confirmed | Read `hand_select.can_confirm`/`selected_cards`, not `player.hand` |
| `card_select` infinite loop | Kept sending `cancel_selection` (invalid) | Read nested `card_select.cards`; handle the preview-confirm step |
| `game_over` infinite error loop | Sent `proceed` forever (not a valid action) | Read `game_over.options`, not top-level `options` |
| Wrong character played | Confirmed into Regent, not Silent | Always explicitly re-select Silent before ever confirming |
| Engine freeze mid-combat | Bot idled forever, no error | Stuck-detector + kill/relaunch/continue recovery |
| Crash on network timeout | Uncaught `ReadTimeout` killed the process | Catch `RequestException` alongside `ApiError` |
| Action reports "ok" but nothing changes | `play_card` on Calculated Gamble burned 419/500 actions replaying itself -- hand/energy/block/enemy HP never budged despite every post succeeding | Added a 0.3s `POST_ACTION_DELAY` (actions may need a moment to actually resolve before the next poll), plus a fingerprint-based stale-action detector that escapes after `STALE_ACTION_LIMIT` (4) identical decisions against unchanged decision-relevant state |
| Potion reward loop | `claim_reward` on a potion with a full potion belt returns "ok" and silently does nothing -- looped 90 times | `rewards.py` compares `len(player.potions)` against `max_potion_slots` and skips unclaimable potion rewards; also generalized the stale detector to non-combat screens (it was combat-only, so it never fired here) |
| Cards played into a lagging state (Grand Finale) | Grand Finale (60 dmg to ALL enemies) ends the fight instantly, but the mod kept serving the pre-play combat state for several polls -- the bot fired ~5 more cards into a dead combat, producing bogus errors, until the stale detector rescued it | Added a **settle gate**: after a successful action, poll (up to `SETTLE_MAX_POLLS`) until the decision-relevant state actually differs from the pre-action snapshot before deciding again. Cheap local polls replace wasted actions. Still bounded, so a genuinely no-op action falls through to the stale escape |
| Safety nets force-ending turns mid-fight | `"state"` (a deliberate no-op poll, e.g. during the enemy's phase) was counted as a repeated *decision*, so the stale/cycle detectors concluded "stuck" and forced `end_turn` -- once dumping 3 energy and three playable Defends into a 23-damage hit. This looked like bad combat logic but was the loop corrupting good decisions | `loop.py` short-circuits `action == "state"` *before* the detectors. Genuine freezes remain `StuckDetector`'s job. **Any new no-op action must skip those detectors too.** |
| Reward screens ping-ponging | `rewards` claim_reward opened the card screen -> nothing scored well enough -> `skip_card_reward` returned to rewards with the card still unclaimed -> repeat, 298 times. The stale detector cannot see this: the state genuinely changes each step | `rewards.py` remembers floors where it already skipped a card reward and stops re-claiming them; plus a general loop-level **cycle detector** (a full window of decisions containing only 1-2 distinct ones) as backstop |
| Multi-card "Choose N" screens stuck | `card_select` with `screen_type: simple_select` and prompt "Choose 2 Common Cards to Add to Your Deck" -- unlike `hand_select`, it reports **no `selected_cards`**, so the bot re-picked its single best card forever (81 identical `select_card {'index': 4}` calls) and never reached confirm | `misc_screens.py` keeps its own per-visit record of picked indices (keyed by a screen signature so it resets between visits) and treats **`can_confirm`** as the authoritative "enough selected" signal -- the prompt's count isn't machine-readable |
| Multiple bot processes at once | `TaskStop` killed the shell wrapper but left the `python -m bot.main` child alive, so up to three bots (some running pre-fix code) played the same game simultaneously, issuing conflicting actions. Presented as "the bot doesn't have the new code" and as phantom loops | Kill by PID (`Get-CimInstance Win32_Process -Filter "Name='python.exe'"` → `Stop-Process`) and confirm none remain before starting a new run. **Always verify exactly one process is alive after a restart.** |
| Walked past every shop without buying | Shop items use **`category`** (`card`/`relic`/`potion`/`card_removal`), not `type`/`item_type` as guessed -- no branch ever matched, so it always fell through to `proceed` | Rewrote `shop.py` against the verified payload; also use the API's own `is_stocked`/`can_afford` flags rather than recomputing affordability. Names are `card_name`/`relic_name`/`potion_name`, and `card_removal` items have no name at all |
| `bundle_select` error loop | "Choose 1 of 2 packs of cards" (Scroll Boxes relic) read top-level `bundles` instead of `bundle_select.bundles`, fell through to `cancel_bundle_selection` -- invalid there, `can_cancel` is false -- and errored repeatedly | Read the nested payload; score bundles by the summed synergy of all their cards; handle the same select-then-`preview_showing`-then-confirm flow `card_select` uses. Found straight from the `screen_raw` logging added for unverified screens |
| Invalid `proceed` on menus | Transitional menu screens with no options got `proceed`, which isn't valid there, producing bursts of errors | `menu.py` fallbacks return `state` (poll) instead |
| Block waived too aggressively | A "would I survive the turn?" safety floor let it take an 11-damage hit at 44/70 rather than play a Defend it was holding, and end turns at 8 HP with two playable Defends | Block is now waived only for genuine chip damage (`max(3, 5% of max HP)`), never merely because the turn is survivable -- HP is a run-long resource. Added a leftover-energy step so it never ends a turn holding block with energy to spare and real incoming damage |

| **The test suite force-killed the real game** | Slay the Spire 2 shut down and relaunched itself every few minutes, *including with no bot process running* -- it looked like an outside cause, and a 3-minute process watch caught nothing. Steam's `logs/console_log.txt` showed the mechanism: a burst of ~7 `steam://rungameid/2868840` requests in 1-2s, then `Game process removed`, then a fresh process. Asking Steam to launch an already-running game makes it kill the running instance first (`WaitingPrevProcess`) | `test_loop.py` drives `BotLoop.run()` from **seven** places and none stubbed `kill_and_relaunch`; those tests reach `_abandon_preexisting_run`, which really does `taskkill` the game and `os.startfile("steam://...")`. They also monkeypatch `time.sleep` to a no-op, erasing the 3s gap -- so one pytest run fired seven relaunches in three seconds. `tests/conftest.py` now has an autouse fixture neutralising `kill_and_relaunch` in both `bot.recovery` and `bot.loop` and making `os.startfile` raise. **Verified 0 relaunches per test run, was 7.** Any new side-effecting call needs the same treatment |
| Upgraded cards invisible to the card database | Upgraded cards arrive with the upgrade in the **name** (`Strike+`, `Afterimage+`), and every lookup in `cards.py` is keyed on the base name -- so `Afterimage+` scored **0.0 instead of 89.0**. The bot went progressively blind to its own best cards as a run upgraded them: rating them as junk to remove, ignoring them when reading the deck's archetype, and (every candidate tying at 0) picking the *first card offered* as an upgrade target. Seen live removing `Strike+` and `Neutralize+` while a `Normality` curse sat in the deck | `cards.base_name()` strips the suffix and `card_info()` normalises through it; every raw `STARTERS_BY_NAME` membership test goes through it too. Also: `pick_upgrade_target` skips already-upgraded cards (upgrading again gains nothing), `removal_priority` detects curses/status via `rarity` as well as `type` (live screens don't always send `type`), and between two identical starters the un-upgraded copy is cut first |
| The test suite polluted the real stats file | `scripts/relic_report.py` reported "40 runs, avg floor 6.4" when real runs averaged ~13. `RunRecorder` calls `relic_stats.record_run` on every `game_over`; those tests pass `tmp_path` for the recorder's own logs but the stats path is a module constant, so **54 synthetic rows** had accumulated against 21 real runs | Autouse fixture in `conftest.py` monkeypatches `relic_stats.STATS_DIR`/`HISTORY_FILE` to `tmp_path`. Synthetic rows are identifiable exactly (`relics == ["Ring of the Snake"]`) |
| Never retained a card | One run was offered "Choose a card to Retain." **41 times and retained nothing all 41** | `can_confirm` means "confirming is legal", not "enough is selected" -- on an *optional* prompt it is true the moment the screen opens, and the handler read that as done. `decide_hand_select` now checks an `_optional_pick_limit` first. Retain ranking deliberately does **not** lead with `score_card` (a deck-building metric, on which a starter Defend at 30 outranks Nightmare at 28): skip starters, take the most expensive, prefer Sly, then deck score. Note Sly reads "discarded **before the end of your turn**", so the end-of-turn discard does *not* trigger it -- retaining a Sly card preserves the free play |
| Sly pitches ranked by pick rate | Forced to discard, it pitched a Sly attack whose damage the enemy's Block absorbed entirely, keeping a Sly *block* card that would have covered the incoming hit. Both are Sly, so both landed in the same tier and the tie-break knew nothing about the board | `_sly_realised_value()` scores what the free play actually does *this turn*: damage capped by enemy Block, block capped by unblocked incoming, poison counted in full (it ignores Block) |

## Running / testing

- `pytest tests/` — fast, no game needed, fixtures capture every real payload
  shape found so far. **The suite must never touch the real game or the real
  stats files.** `conftest.py` has autouse fixtures enforcing both; it took a
  live evaluation set being wrecked mid-run to notice they were missing.
- `python -m bot.main [--max-actions N]` — needs the game open with mods
  enabled; drives its own runs (menu -> character select -> embark -> loop
  forever) unless bounded.
- Logs: `logs/run_<timestamp>.jsonl`, one line per decision (state summary +
  action + outcome delta), plus combat hand/energy detail and the raw
  payload for any still-unverified screen. This is the primary debugging
  tool — most bugs above were found by reading this after a live run rather
  than by inspection.

## Future direction

Logs are schema'd as (state, action, outcome) specifically so a
self-improving version built on top of this rules-based one later has
training traces to work from without redoing the logging design.
