"""Combat decision engine.

Called once per poll while state_type is monster/elite/boss and it's our play
phase. Returns a single (action, fields) tuple -- the hand re-indexes after
every play_card, so the loop always re-fetches state between actions rather
than batching a multi-card plan against a stale hand snapshot.

Turn priority:
  1. Lethal: finish off any enemy whose remaining *effective* HP (actual HP
     minus any Poison already ticking down, since that kills them before
     their next turn regardless of anything we do) a card's total damage
     this turn can reach -- direct damage plus shivs it generates, scaled by
     Vulnerable if the target already has it. Enemies poison alone will kill
     are skipped entirely -- no card needed, no point spending one.
  1.2. Scaling buffs: Dexterity/Strength gains go before anything they scale
     (Strength pumps Attacks, Dexterity pumps Block), unless we're under real
     pressure and can't afford both the buff and enough block to survive.
  1.5. Free damage: any playable 0-cost attack (Shivs, Neutralize, Backstab...)
     gets played immediately -- it never competes with anything else for
     energy, so there's no reason one should still be sitting in hand when
     the turn ends.
  1.7. Mitigation: Weak / enemy-Strength reduction go before the block
     decision, so block is sized against the *reduced* hit rather than the
     full one (blocking first wastes part of it).
  2. Survive: block is need-based, not automatic. If nothing's actually
     attacking (or the only "attacker" left will die to Poison first, and no
     damaging Curse is sitting in hand) there's nothing to block against.
     When there is, block it -- unless it's mere chip damage we're healthy
     enough to absorb for tempo (take 1 to deal 6). Note the asymmetry: HP is
     a run-long resource, so "I'd survive the turn" is NOT sufficient reason
     to eat a real hit. When block is worth playing, use the smallest card
     that actually covers the gap rather than overshooting.
  3. Value: energy-efficient debuff/value plays (poison, weak, sly/discard-
     engine cards), scored via cards.score_card.
  4. Damage: spend remaining energy on attacks -- Vulnerable-applying attacks
     on an as-yet-undebuffed target go first (so everything played after
     benefits from the +50%), then by total effective damage, targeting the
     lowest-(effective-)HP enemy.
  5. Leftover energy: rather than end the turn with energy unspent, play a
     block card against any real incoming damage -- free value at that point.
  6. Final scan: still holding energy and a playable card? Play the best one.
     Energy doesn't carry over, so every earlier step's bar (minimum synergy
     score, incoming damage required) can strand a card that's still worth
     more than nothing.
  7. End turn.
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional

from ..game_state import GameState
from . import cards as card_db
from . import potions as potion_db

_DAMAGE_RE = re.compile(r"[Dd]eal (\d+) damage")
_BLOCK_RE = re.compile(r"[Gg]ain (\d+) Block")
# X-cost cards spell the amount as a literal "X" (Malaise: "Apply X Weak"),
# so a digits-only pattern silently misses them.
_VULNERABLE_RE = re.compile(r"[Aa]pply (\d+|X(?:\+\d+)?) Vulnerable")
_SHIV_RE = re.compile(r"[Aa]dd (\d+)[^.]*Shiv")
_TIMES_RE = re.compile(r"(\d+) times")
_INTENT_NUM_RE = re.compile(r"(\d+)")
# Multi-hit intents read "4x4" (four hits of four).
_INTENT_MULTI_RE = re.compile(r"(\d+)\s*[xX*]\s*(\d+)")
# The game annotates conditional cards with their live value, e.g. Knife Trap:
# "Play every Shiv in your Exhaust Pile on the enemy. (Plays 0 Shivs)".
_PLAYS_N_RE = re.compile(r"\(Plays (\d+)\b", re.IGNORECASE)
_DISCARDS_HAND_RE = re.compile(r"[Dd]iscard your Hand")
# Hand caps at 10 (confirmed from logged hand sizes: 47 observations at 10,
# none above), so drawing past it throws the drawn cards away.
HAND_LIMIT = 10
_DRAWS_RE = re.compile(r"[Dd]raw (?:\d+|cards?)", re.IGNORECASE)
_GAINS_ENERGY_RE = re.compile(r"[Gg]ain.*(?:energy_icon|Energy)", re.IGNORECASE)
# "Whenever you play a card this turn, ..." -- Choke, Strangle. Value comes
# from the cards played afterwards, so these want to be first.
_SCALES_WITH_PLAYS_RE = re.compile(r"[Ww]henever you play a card this turn", re.IGNORECASE)
_SELF_DAMAGE_RE = re.compile(r"(?:take|lose)\s+(\d+)\s+(?:damage|HP)", re.IGNORECASE)
# Two wordings for one mechanic: Infection "take 3 damage", Beckon "lose 6 HP".
# Matching only the first made Beckon -- 6 HP a turn, dealt by the act 1 boss
# 1-2 at a time -- completely invisible to incoming-damage and discard ranking.
_GAIN_BUFF_RE = re.compile(r"[Gg]ain \d+ (?:Dexterity|Strength)")
_WEAK_RE = re.compile(r"[Aa]pply (\d+|X(?:\+\d+)?) Weak")
_POISON_RE = re.compile(r"[Aa]pply (\d+) Poison")
# Order-dependent damage. These scale off things that change as the turn is
# played out, so both their damage estimate and their play order matter.
_PER_SKILL_IN_HAND_RE = re.compile(r"[Dd]eal (\d+) damage for each Skill in your Hand")
_PER_ATTACK_PLAYED_RE = re.compile(r"[Dd]eal (\d+) damage for each Attack already played")
_LESS_PER_CARD_IN_HAND_RE = re.compile(r"[Dd]eals? (\d+) less damage for each other card in your Hand")
_PER_CARD_DISCARDED_RE = re.compile(r"(\d+) additional damage for each card discarded")
_CHEAPER_PER_SKILL_RE = re.compile(r"[Cc]osts (\d+) less .* for each Skill played")

PLAY_EARLY = 2  # wants a full hand / unspent resources (Flechettes)
PLAY_NORMAL = 1
PLAY_LATE = 0  # wants the turn's work already done (Finisher, Precise Cut)


def _discards_whole_hand(card: dict[str, Any]) -> bool:
    """Shadow Step / Calculated Gamble: the whole hand goes, not one card."""
    return bool(_DISCARDS_HAND_RE.search(card.get("description", "") or ""))


def _forces_a_discard(card: dict[str, Any]) -> bool:
    """Cards with "Discard N card(s)" attached as a *cost* (Survivor, Prepared,
    Dagger Throw). Excludes "Discard your Hand", which is a whole different
    effect that wants a full hand, not an empty one."""
    description = card.get("description", "")
    if _DISCARDS_HAND_RE.search(description):
        return False
    return bool(_DISCARDS_RE.search(description))


_STRIPS_ARTIFACT_RE = re.compile(r"remove all artifact", re.IGNORECASE)


_RECURRING_RE = re.compile(r"at the start of (?:your|each) turn", re.IGNORECASE)


def _enemy_artifact(enemy: dict[str, Any]) -> int:
    """Artifact stacks on this enemy. "Negates 2 debuffs" -- so every debuff we
    apply while it stands is simply eaten, and applying one is a wasted card."""
    for status in enemy.get("status", []):
        if "ARTIFACT" in (status.get("id") or status.get("name") or "").upper():
            return status.get("amount") or 0
    return 0


def _strips_artifact(card: dict[str, Any]) -> bool:
    """Expose: "Remove all Artifact and Block from the enemy"."""
    return bool(_STRIPS_ARTIFACT_RE.search(card.get("description", "") or ""))


def _applies_debuff(card: dict[str, Any]) -> bool:
    """Does this card try to put a debuff on an enemy? Those are the plays
    Artifact eats."""
    if _card_applies_weak(card) or _card_applies_vulnerable(card):
        return True
    return _card_poison(card) > 0


def _debuff_is_wasted(card: dict[str, Any], enemy: dict[str, Any]) -> bool:
    """A debuff aimed at an Artifact-shielded enemy accomplishes nothing --
    unless the card strips the Artifact itself, which is the whole point of
    playing Expose first."""
    if not _applies_debuff(card) or _strips_artifact(card):
        return False
    # Only when the debuff is the card's *whole* value. Neutralize ("Deal 6
    # damage. Apply 2 Weak") still deals its 6 whether or not the Weak is
    # eaten -- calling it wasted made the bot end turns holding a free attack
    # with 2 energy left, against an enemy on 10 HP.
    if _card_damage(card) > 0 or _card_block(card) > 0 or _card_shiv_count(card) > 0:
        return False
    # A Power re-applies its debuff every turn, so Artifact eats the first
    # application and every later one lands. Burning a charge is a *cost*, not
    # a waste -- Noxious Fumes ("At the start of your turn, apply 2 Poison to
    # ALL enemies") out-damages a single Strike over any fight long enough to
    # matter, and was being written off entirely.
    if _is_power(card) or _RECURRING_RE.search(card.get("description", "") or ""):
        return False
    return _enemy_artifact(enemy) > 0


_DRAWS_CARDS_RE = re.compile(r"\bdraw\b", re.IGNORECASE)


def _play_timing(card: dict[str, Any], hand: Optional[list[dict[str, Any]]] = None) -> int:
    """When in the turn this card wants to be played.

    Flechettes ("damage for each Skill in your Hand") shrinks every time we
    play a skill, and Choke/Strangle ("whenever you play a card this turn")
    only collect from cards played afterwards -- both must go first.
    Finisher ("for each Attack already played"), Precise Cut ("less damage for
    each other card in your Hand"), Memento Mori ("for each card discarded")
    and Pinpoint ("costs less for each Skill played") all grow as the turn
    progresses, so they go last.

    Cards that discard as a *cost* (Survivor: "Gain 8 Block. Discard 1 card.")
    also go last when we can afford the rest of the hand anyway -- playing
    Survivor first throws away a card we were about to play, while playing it
    last costs nothing because there's nothing left worth keeping. The
    exception is a hand holding a Sly card, where discarding is a *benefit*
    (it plays for free), so the card goes early to trigger it.
    """
    description = card.get("description", "")
    # Expose strips Artifact (and Block) before applying its own Vulnerable.
    # Every other debuff in hand is wasted until it has been played, so it
    # goes first -- the same reasoning as Choke, one step earlier in the turn.
    if _strips_artifact(card):
        return PLAY_EARLY
    # Choke/Strangle: they collect from every card played after them.
    if _PER_SKILL_IN_HAND_RE.search(description) or _SCALES_WITH_PLAYS_RE.search(description):
        return PLAY_EARLY
    if (
        _PER_ATTACK_PLAYED_RE.search(description)
        or _LESS_PER_CARD_IN_HAND_RE.search(description)
        or _PER_CARD_DISCARDED_RE.search(description)
        or _CHEAPER_PER_SKILL_RE.search(description)
    ):
        return PLAY_LATE

    # Drawing early expands what the rest of the turn can spend energy on;
    # drawing last is drawing into a turn that is already over. Reported live:
    # two energy, Backflip ("Gain 8 Block. Draw 2 cards") and Defend in hand,
    # and the bot spent the first energy on Defend. Cards that draw as part of
    # a *discard cost* are handled by the branch below, which has its own
    # reasons for going last.
    if _DRAWS_CARDS_RE.search(description) and not _forces_a_discard(card):
        return PLAY_EARLY

    if _discards_whole_hand(card):
        return PLAY_LATE

    if hand is not None and _forces_a_discard(card):
        has_sly = any(
            card_db.is_sly(c) and c.get("index") != card.get("index") for c in hand
        )
        return PLAY_EARLY if has_sly else PLAY_LATE

    return PLAY_NORMAL


def _contextual_damage(card: dict[str, Any], hand: list[dict[str, Any]]) -> Optional[int]:
    """Actual damage for cards whose printed number is per-something, given the
    current hand. Returns None when the card isn't context-scaled.

    Without this the flat regex reads Flechettes as a 5-damage card, badly
    under-estimating it for lethal checks (it's 5 *per skill in hand*).
    """
    description = card.get("description", "")

    m = _PER_SKILL_IN_HAND_RE.search(description)
    if m:
        skills = sum(
            1
            for c in hand
            if (c.get("type") or "").lower() == "skill" and c.get("index") != card.get("index")
        )
        return int(m.group(1)) * skills

    m = _LESS_PER_CARD_IN_HAND_RE.search(description)
    if m:
        base_match = _DAMAGE_RE.search(description)
        base = int(base_match.group(1)) if base_match else 0
        others = max(0, len(hand) - 1)
        return max(0, base - int(m.group(1)) * others)

    return None

# Damage the hand must be able to land before it is worth waking a sleeping
# enemy early. Below this the free turn is better spent on Powers and setup:
# Lagavulin Matriarch re-blocks 12 a turn, so a lone Strike is absorbed
# outright while the boss starts hitting for 23 sooner than it had to.
ASLEEP_WAKE_BURST = 30

SHIV_BASE_DAMAGE = 4
VULNERABLE_MULTIPLIER = 1.5
# Weak, as the game states it: "Attacks deal 25% less damage".
WEAK_DAMAGE_REDUCTION = 0.25
# With the countdown this low, escaping outranks even a Block we need: if the
# clock runs out we die regardless of how much Block is up.
DEATH_CLOCK_LAST_CHANCE = 1
# While the clock has slack the escape only displaces a weak play. Roughly a
# basic Strike: anything hitting harder, or any Power, is the better use of
# the energy because the fight still has to be won, not merely survived.
DEATH_CLOCK_WEAK_PLAY_BAR = 8.0
# Not worth a card and an energy to prevent less than this.
MIN_MITIGATION_VALUE = 3
SAFETY_HP_PCT_FLOOR = 0.25  # don't let a voluntarily-unblocked hit drop us below this fraction of max HP...
SAFETY_HP_ABS_FLOOR = 10  # ...or below this absolute amount, whichever is higher
# Only damage at or below this counts as "chip" worth eating for tempo (the
# take-1-to-deal-6 trade). Anything bigger gets blocked even at full HP: HP is
# a run-long resource in StS, not a per-fight one, so surviving the turn is a
# floor, not the goal. Tuned from a live regression where a purely
# survive-the-turn floor let it take an 11-damage hit at 44/70 rather than
# spend a Defend it was holding.
CHIP_DAMAGE_PCT = 0.05
CHIP_DAMAGE_ABS = 3
# "Race" mode for long elite/boss fights: accept materially more chip damage
# per turn in exchange for ending the fight sooner. See `_should_race`.
RACE_CHIP_MULTIPLIER = 3.0
RACE_MIN_TURNS = 3.0  # only worth racing when the fight really is a grind
RACE_HP_SAFETY_MARGIN = 1.6  # need real headroom above the safety floor
# Cap on how many turns of poison payout to credit. Beyond this the estimate
# is guesswork, and over-valuing poison would starve real damage.
POISON_MAX_HORIZON = 6.0
# A block card is "efficient" when the incoming hit uses at least this much of
# it -- blocking 1 damage with a 5-block Defend wastes most of the card and an
# energy that could have dealt damage, but repeatedly eating 3-4 damage adds up
# fast over a long fight. At 0.5 a 5-block Defend is played against 3+ damage.
BLOCK_EFFICIENCY_RATIO = 0.5


def _card_damage(card: dict[str, Any]) -> int:
    """Sums every 'Deal N damage' clause and applies simple 'twice'/'N times'
    multipliers (e.g. Ricochet's "3 damage... 4 times", Dagger Spray's
    "4 damage... twice"). Doesn't attempt full conditional-effect modeling."""
    desc = card.get("description", "")
    total = sum(int(m) for m in _DAMAGE_RE.findall(desc))
    if re.search(r"\btwice\b", desc, re.IGNORECASE):
        total *= 2
    else:
        m = _TIMES_RE.search(desc)
        if m:
            total *= int(m.group(1))
    return total


def _card_block(card: dict[str, Any]) -> int:
    m = _BLOCK_RE.search(card.get("description", ""))
    return int(m.group(1)) if m else 0


def _card_shiv_count(card: dict[str, Any]) -> int:
    m = _SHIV_RE.search(card.get("description", ""))
    return int(m.group(1)) if m else 0


_SHIVS_PER_DISCARD_RE = re.compile(
    r"add (\d+) shivs? into your hand for each card discarded", re.IGNORECASE
)


def _discard_is_the_payoff(card: dict[str, Any], gs: GameState) -> bool:
    """A "Discard your Hand" card whose discard *is* the value, right now.

    Storm of Steel ("Discard your Hand. Add 1 Shiv into your Hand for each card
    discarded.") turns every discarded card into a Shiv, and a discarded Sly
    card plays for free. The rule that holds hand-discards back until the
    energy is spent was written for cards where the discard is only a cost. It
    wrote Storm off on a live turn with the Entomancer at 8 HP, the bot on 3
    and Haze (Sly) affordable -- so the bot paid 3 energy for Haze and died,
    where Storm would have made four Shivs and played Haze free.
    """
    m = _SHIVS_PER_DISCARD_RE.search(card.get("description") or "")
    if not m:
        return False
    others = [c for c in gs.hand if c.get("index") != card.get("index")]
    if not others:
        return False
    if any(card_db.is_sly(c) for c in others):
        return True
    shivs = int(m.group(1)) * len(others)
    shiv_bonus = _shiv_damage_bonus(gs)
    for enemy in gs.enemies:
        if _effective_hp(enemy) <= 0:
            continue
        per_shiv = _effective_damage(card, enemy, shiv_bonus, gs.hand) / max(1, _card_shiv_count(card))
        if per_shiv * shivs >= _damage_to_kill(enemy):
            return True
    return False


def _card_applies_vulnerable(card: dict[str, Any]) -> bool:
    return _nonzero_amount(_VULNERABLE_RE.search(card.get("description", "")))


def _nonzero_amount(match: "re.Match[str] | None") -> bool:
    """True when a parsed effect amount is actually greater than zero.

    An X-cost card resolved at 0 energy reads "Apply 0 Weak" / "Enemy loses 0
    Strength" -- the regex still matches, but the card does nothing at all and
    Malaise *Exhausts*, so playing it there destroys it for no effect. A live
    run did exactly that.
    """
    if match is None:
        return False
    raw = match.group(1)
    if raw.upper().startswith("X"):
        return True  # unresolved: assume it does something
    return int(raw) > 0


def _card_applies_weak(card: dict[str, Any]) -> bool:
    return _nonzero_amount(_WEAK_RE.search(card.get("description", "")))


# Intent types that genuinely deal no damage. Everything *else* is treated as
# an attack if its label or text carries a number.
#
# This used to whitelist the single string "Attack", which is silently fatal
# the first time an attack arrives under another name. Waterfall Giant's
# finisher is `type: "DeathBlow", label: "48"` -- "It will attack you for 48
# damage before being destroyed" -- so the bot read incoming damage as **0**,
# did not block, and died to a hit it could see perfectly well. A denylist
# fails the safe way round: an unknown *harmless* type is over-blocked, an
# unknown *attack* is still counted.
_NON_DAMAGING_INTENTS = frozenset({
    "buff", "debuff", "heal", "stun", "statuscard", "status", "unknown",
    "sleep", "escape", "none", "shift", "summon",
})
# "It will attack you for 48 damage", "intends to Attack for 15 damage"
_INTENT_TEXT_DAMAGE_RE = re.compile(r"for\s+(\d+)\s+damage", re.IGNORECASE)


def _is_escaping(enemy: dict[str, Any]) -> bool:
    """True when this enemy intends to leave the fight.

    An escaping enemy is the *last* chance to kill it -- and in this game they
    typically leave with whatever they stole. It deals no damage, so every
    "is it attacking?" filter skips it and the lethal step, which sorts by how
    easy each enemy is to finish, would happily kill the one that was going to
    stay put anyway.
    """
    return any(
        (intent.get("type") or "").strip().lower() == "escape"
        for intent in enemy.get("intents", [])
    )


# Waterfall Giant at 0 HP becomes a 999,999,999 HP husk whose only intent is a
# DeathBlow -- "It will attack you for 51 damage before being destroyed" --
# and Gas Bomb does the same. That enemy is already beaten: nothing we deal to
# it matters, the hit comes regardless, and the fight ends after it. So on
# that turn, no damage -- only Block (the user's rule). Run 1 spent its last
# turn attacking the husk.
#
# Only the husk counts. Gas Bomb shows the same DeathBlow intent with real HP
# (7/7) -- killing it may well stop the blast, so it stays a normal target.
# The husk is recognisable by its placeholder HP.
HUSK_MIN_HP = 100_000_000


def _is_spent(enemy: dict[str, Any]) -> bool:
    if (enemy.get("max_hp") or 0) < HUSK_MIN_HP:
        return False
    for intent in enemy.get("intents", []) or []:
        if (intent.get("type") or "").strip().lower() == "deathblow":
            return True
        if "before being destroyed" in (intent.get("description") or "").lower():
            return True
    return False


def _fight_ends_this_turn(gs: GameState) -> bool:
    """Every enemy still standing is spent: this is the last hit of the fight."""
    live = [e for e in gs.enemies if (e.get("hp") or 0) > 0]
    return bool(live) and all(_is_spent(e) for e in live)


# Positional damage, introduced in Act 2. The player picks up **Surrounded**
# ("Receive 50% more damage if attacked from behind. Use targeting cards or
# potions to change your orientation") and any enemy actually behind us is
# flagged **Back Attack** ("Deals 50% more damage when it is attacking you
# from behind").
#
# None of this was modelled: a Crusher telegraphing 20 was read as 20 when it
# lands for 30, so the bot under-blocked by half against precisely the enemies
# hitting hardest. Observed live at floor 33 with the player on 6 HP.
#
# The flag lives on the *enemy*, so the correction needs nothing but the
# enemy itself.
BACK_ATTACK_MULTIPLIER = 1.5


def _enemy_attacks_from_behind(enemy: dict[str, Any]) -> bool:
    for status in enemy.get("status", []) or []:
        name = (status.get("id") or status.get("name") or "").upper().replace("_", " ")
        if "BACK ATTACK" in name:
            return True
    return False


def _enemy_attack_damage(enemy: dict[str, Any]) -> int:
    """This enemy's own telegraphed attack damage (0 if it isn't attacking).
    Multi-hit aware -- see `_intent_damage`."""
    total = 0
    for intent in enemy.get("intents", []):
        if (intent.get("type") or "").strip().lower() in _NON_DAMAGING_INTENTS:
            continue
        damage = _intent_damage(intent.get("label", ""))
        if not damage:
            # Some intents carry the number only in their prose.
            m = _INTENT_TEXT_DAMAGE_RE.search(
                intent.get("description") or intent.get("text") or ""
            )
            damage = int(m.group(1)) if m else 0
        total += damage
    if total and _enemy_attacks_from_behind(enemy):
        total = int(total * BACK_ATTACK_MULTIPLIER)
    return total


# Fights that end the moment one enemy dies (its minions leave / the encounter
# resolves). Some encounters say so outright: a summoned enemy carries the
# status Minion ("Minions abandon combat without their leader"), which makes
# every non-Minion beside it a leader -- see `_is_leader`. Where nothing is
# flagged this falls back to a *heuristic* over the name plus "does it summon
# things", the most likely part of combat.py to be wrong for an unfamiliar
# encounter. Getting it wrong is not catastrophic: it only relaxes the "would
# the other enemies kill me?" guard on a kill we can already reach this turn.
_LEADER_NAME_HINTS = (
    "leader", "king", "queen", "champion", "chief", "captain", "matriarch",
    "commander", "elder", "master", "core", "heart", "head",
)
_SUMMONER_HINTS = ("summon", "spawn", "call ", "reinforce")


def _is_likely_leader(enemy: dict[str, Any]) -> bool:
    name = (enemy.get("name") or "") + " " + (enemy.get("entity_id") or "")
    lowered = name.lower()
    if any(hint in lowered for hint in _LEADER_NAME_HINTS):
        return True
    # An enemy that summons reinforcements is normally the one holding the
    # encounter together.
    text = " ".join(
        (s.get("description") or "") for s in enemy.get("status", [])
    ) + " " + " ".join((i.get("description") or "") for i in enemy.get("intents", []))
    return any(hint in text.lower() for hint in _SUMMONER_HINTS)


def _enemy_status_matches(enemy: dict[str, Any], name: str, text: str) -> bool:
    """True if the enemy carries a status by this name, or with this text."""
    for status in enemy.get("status", []) or []:
        status_name = (status.get("id") or status.get("name") or "").upper().replace("_", " ")
        description = (status.get("description") or status.get("text") or "").lower()
        if name in status_name or text in description:
            return True
    return False


def _is_minion(enemy: dict[str, Any]) -> bool:
    return _enemy_status_matches(enemy, "MINION", "abandon combat without their leader")


def _is_leader(enemy: dict[str, Any], enemies: list[dict[str, Any]]) -> bool:
    """The enemy holding this encounter together: named by its Minions when
    they are flagged, otherwise guessed by `_is_likely_leader`."""
    if _is_minion(enemy):
        return False
    if any(_is_minion(e) for e in enemies if e is not enemy):
        return True
    return _is_likely_leader(enemy)


def _resummons(enemy: dict[str, Any]) -> bool:
    """Is this enemy calling in more minions right now (Ovicopter's eggs)?"""
    for intent in enemy.get("intents", []) or []:
        if (intent.get("type") or "").strip().lower() == "summon":
            return True
        text = (intent.get("description") or "").lower()
        if any(hint in text for hint in _SUMMONER_HINTS):
            return True
    return False


def _flagged_leader(
    active_enemies: list[dict[str, Any]], all_enemies: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """The one non-Minion in a fight whose other enemies are flagged Minion."""
    if not any(_is_minion(e) for e in all_enemies):
        return None
    leaders = [e for e in active_enemies if not _is_minion(e)]
    return leaders[0] if len(leaders) == 1 else None


# Minions are not ignored: Eye with Teeth killed on our turn adds no Status
# cards (216 logged turns: 0.31 on average, against 2.5 when left alive), so a
# one-card minion kill that denies an action is usually worth taking. It stops
# being worth it once the leader is close -- the minions leave with it, and
# while it lives it re-summons them. Run 10 spent ten rounds killing
# Ovicopter's Tough Eggs with Ovicopter at 51 HP and Strikes landing for 20+,
# and died. Within this many turns of damage, the leader takes all of it.
LEADER_FOCUS_TURNS = 2.0


def _leader_in_reach(
    leader: dict[str, Any], hand: list[dict[str, Any]], energy: int, shiv_bonus: int
) -> bool:
    """Would this turn's damage, repeated, kill the leader within
    `LEADER_FOCUS_TURNS` turns?"""
    per_turn = _reachable_damage(hand, leader, energy, shiv_bonus)
    return per_turn > 0 and _damage_to_kill(leader) <= per_turn * LEADER_FOCUS_TURNS


def _incoming_attack_count(enemies: list[dict[str, Any]]) -> int:
    return sum(
        1
        for e in enemies
        for intent in e.get("intents", [])
        if intent.get("type") == "Attack"
    )


def _play_limit(gs: GameState) -> int | None:
    """Cards we are allowed to play this turn, when something caps it.

    Ringing reads "You can only play 1 card this turn". With a cap of one, the
    turn is a single decision and the usual ordering logic is meaningless --
    whatever we pick is the whole turn, so it has to be the highest-value
    single card rather than the first step of a sequence.

    Matched on the status name *and* on the effect text, because no live
    Ringing payload has been captured yet and the name may differ; the text
    form is the more reliable of the two.
    """
    for status in gs.player.get("status", []):
        name = (status.get("id") or status.get("name") or "").upper()
        desc = (status.get("description") or status.get("text") or "")
        if "RINGING" in name:
            return 1
        m = _PLAY_LIMIT_RE.search(desc)
        if m:
            return int(m.group(1))
    return None


_PLAY_LIMIT_RE = re.compile(
    r"can only play (\d+) card", re.IGNORECASE
)


# The Gambit: "Gain 50 Block. If you take unblocked attack damage this combat,
# die." Once it is up, chip damage is no longer chip -- one point kills. A
# live run at 69/70 HP ended its turn on 13 Block against 7x2, having played a
# Strike over its last Defend, and died on floor 3.
_UNBLOCKED_IS_LETHAL_RE = re.compile(
    r"take unblocked attack damage[^.]*\bdie\b", re.IGNORECASE
)


def _kills_us_on_any_chip(card_or_status: dict[str, Any]) -> bool:
    text = card_or_status.get("description") or card_or_status.get("text") or ""
    return bool(_UNBLOCKED_IS_LETHAL_RE.search(text))


def _unblocked_damage_is_lethal(gs: GameState) -> bool:
    return any(_kills_us_on_any_chip(s) for s in gs.player.get("status", []) or [])


def _player_has_status(gs: GameState, needle: str) -> bool:
    for status in gs.player.get("status", []):
        name = (status.get("id") or status.get("name") or "").upper()
        if needle.upper() in name:
            return True
    return False


# Recurring damage that lives on a *player status*, not on a card in hand.
# Knowledge Demon's Disintegration reads "At the end of your turn, take 13
# damage" as a Debuff power. `_hand_curse_damage` only scans the hand, so this
# was worth exactly nothing to the incoming estimate: with 20 HP and an 8
# damage intent the bot saw 8 incoming while 21 was actually arriving, and
# blocked for a hit it had already survived on paper.
#
# Same blockable/unblockable split as cards: "take N damage" lands on Block,
# "lose N HP" bypasses it.
_STATUS_TICK_RE = re.compile(
    r"at the end of your turn, take\s+(\d+)\s+damage", re.IGNORECASE
)
_STATUS_HP_LOSS_RE = re.compile(
    r"at the end of your turn, lose\s+(\d+)\s+HP", re.IGNORECASE
)


def _status_self_damage(gs: GameState) -> int:
    """End-of-turn damage from our own statuses that Block can absorb."""
    total = 0
    for status in gs.player.get("status", []) or []:
        text = status.get("description") or status.get("text") or ""
        m = _STATUS_TICK_RE.search(text)
        if m:
            total += int(m.group(1))
    return total


def _status_hp_loss(gs: GameState) -> int:
    """End-of-turn HP loss from our own statuses that Block cannot stop."""
    total = 0
    for status in gs.player.get("status", []) or []:
        text = status.get("description") or status.get("text") or ""
        m = _STATUS_HP_LOSS_RE.search(text)
        if m:
            total += int(m.group(1))
    return total


def _pending_block_from_status(gs: GameState) -> int:
    """Block our own statuses will hand us for free this turn (Plating and
    friends: "At the end of your turn, gain N Block").

    Counting it stops the bot from spending cards to cover damage that's
    already handled -- overblocking wastes both the card and the energy.
    Matched by name and by effect text so unfamiliar equivalents still count.
    """
    total = 0
    for status in gs.player.get("status", []):
        name = (status.get("id") or status.get("name") or "").upper()
        description = (status.get("description") or "").lower()
        amount = status.get("amount") or 0
        if not amount:
            continue
        if "PLATING" in name or "METALLICIZE" in name:
            total += amount
        elif "block" in description and ("end of your turn" in description or "start of your turn" in description):
            total += amount
    return total


def _mitigated_incoming(gs: GameState, enemies: list[dict[str, Any]], raw_incoming: int) -> int:
    """Incoming damage after our own defensive statuses.

    Intangible reduces every hit to 1, so the raw intent total is wildly
    misleading and would have us blocking damage that cannot land.
    """
    if _player_has_status(gs, "INTANGIBLE"):
        return _incoming_attack_count(enemies)
    return raw_incoming


_DOUBLE_DAMAGE_RE = re.compile(r"double[^.]*damage", re.IGNORECASE)


_DUPLICATOR_RE = re.compile(
    r"next card is played an extra time|play(?:s|ed)? an extra time",
    re.IGNORECASE,
)


def _duplicator_active(gs: GameState) -> bool:
    """Is the *next* card we play going to resolve twice?

    The Duplicator potion reads "This turn, your next card is played an extra
    time". It is a one-shot resource attached to whichever card we pick next,
    so the card chosen immediately after drinking it decides the whole value
    of the potion -- spending it on a Defend when a big attack is in hand
    throws it away.
    """
    for status in gs.player.get("status", []):
        text = (status.get("description") or status.get("text") or "")
        name = (status.get("id") or status.get("name") or "")
        if _DUPLICATOR_RE.search(text) or "DUPLICAT" in name.upper():
            return True
    return False


def _player_damage_multiplier(gs: GameState, enemy: dict[str, Any] | None = None) -> float:
    """Our own damage multipliers that apply to *this* attack, right now.

    Read alongside Strength when checking lethal: a doubled hit can finish an
    enemy the printed number cannot, and missing it means passing up the kill.

    Three near-identical texts must NOT count, and each would cause a false
    lethal:

    * **Block doublers** -- Shadowmeld, "Double your Block gain this turn".
    * **Next-turn buffs** -- Shadow Step leaves a status reading "Next turn,
      Attacks deal double damage". It is real, but not yet.
    * **Conditional doublers** -- Tracking, "Weak enemies take double damage
      from Attacks", only applies to a target that actually has Weak.
    """
    multiplier = 2.0 if _duplicator_active(gs) else 1.0
    for status in gs.player.get("status", []):
        text = (status.get("description") or status.get("text") or "")
        lowered = text.lower()
        if _DUPLICATOR_RE.search(text):
            continue  # already counted above
        if "block" in lowered:
            continue
        if "next turn" in lowered:
            continue  # real, but it applies to the turn after this one
        if not _DOUBLE_DAMAGE_RE.search(text):
            continue
        if "weak" in lowered:
            # Only counts against a target that is actually Weakened.
            if enemy is None or not _enemy_has_weak(enemy):
                continue
        multiplier *= 2.0
    return multiplier


def _player_strength(gs: GameState) -> int:
    """Our own Strength, which can be negative.

    Strength adds to *every hit*, exactly as Dexterity adds to every Block --
    but only Dexterity was ever read. With -6 Strength the bot still valued a
    6-damage Strike at 6 when it actually deals 0, so it burned energy on dead
    attacks and, worse, claimed lethals that could not land.
    """
    for status in gs.player.get("status", []):
        name = (status.get("id") or status.get("name") or "").upper()
        if "STRENGTH" in name:
            amount = status.get("amount") or 0
            return -amount if "LOSE" in name else amount
    return 0


def _player_dexterity(gs: GameState) -> int:
    for status in gs.player.get("status", []):
        name = (status.get("id") or status.get("name") or "").upper()
        if "DEXTERITY" in name:
            amount = status.get("amount") or 0
            return -amount if "LOSE" in name else amount
    return 0


def _effective_block(card: dict[str, Any], dexterity: int) -> int:
    """Block this card actually grants.

    Dexterity is deliberately NOT applied. The game reports the card with the
    modifier already in it -- at Dexterity -2 a Defend reads "Gain 3 Block",
    not "Gain 5 Block" -- so adding it again double-counted. At Dexterity -4
    it computed 0 for a Defend that really gave 1, which is how four Defends
    got played "for nothing" in a single Lagavulin fight.

    The parameter is kept: every call site passes it, and dropping it would
    touch a dozen signatures for no gain.
    """
    return max(0, _card_block(card))


# "loses" as well as "lose": Piercing Wail says "ALL enemies lose 6
# Strength", but Malaise says "Enemy loses X+1 Strength" -- the singular
# form matched nothing, so a card built entirely around Strength
# reduction contributed zero to the mitigation estimate.
_STRENGTH_DOWN_RE = re.compile(r"loses? (\d+|X) Strength", re.IGNORECASE)
_DISCARDS_RE = re.compile(r"[Dd]iscard (\d+|your Hand|a card)", re.IGNORECASE)


def _enemy_hit_count(enemy: dict[str, Any]) -> int:
    """How many separate attacks this enemy is about to make.

    Needed because per-hit effects scale with it: Thorns costs us once per hit
    we land, and Strength reduction saves us once per hit we take. Multi-hit
    labels look like "5x3".
    """
    hits = 0
    for intent in enemy.get("intents", []):
        if (intent.get("type") or "").strip().lower() in _NON_DAMAGING_INTENTS:
            continue
        label = intent.get("label") or ""
        m = _INTENT_MULTI_RE.search(label)
        # Labels are damage-by-hits: "5x3" is 5 damage three times, confirmed
        # against the intent prose ("Attack for 5 damage 3 times"). The hit
        # count is the *second* number.
        hits += int(m.group(2)) if m else 1
    return hits


def _attack_hits(enemies: list[dict[str, Any]]) -> int:
    """Separate attack hits coming this turn -- per-hit effects scale with it."""
    hits = 0
    for enemy in enemies:
        for intent in enemy.get("intents", []) or []:
            if (intent.get("type") or "").strip().lower() not in ("attack", "deathblow"):
                continue
            m = _INTENT_MULTI_RE.search(intent.get("label") or "")
            hits += int(m.group(2)) if m else 1
    return hits


# Infested Prism's Vital Spark makes every Skill Tainted: playing one adds
# Tainted -- "Take N additional damage from Attacks this turn" -- and it
# stacks (2, 4 ... 16 within one live turn). A Skill's real Block is its
# printed Block minus that cost per attack hit coming, and a draw Skill that
# blocks nothing only makes the hit worse. The user's call: factor it in.
_TAINTED_GAIN_RE = re.compile(r"gain (\d+) tainted", re.IGNORECASE)
_TAINTED_STATUS_RE = re.compile(r"take (\d+) additional damage from attacks", re.IGNORECASE)


def _tainted_gain(card: dict[str, Any]) -> int:
    m = _TAINTED_GAIN_RE.search(card.get("description") or "")
    return int(m.group(1)) if m else 0


def _player_tainted(gs: GameState) -> int:
    """Extra damage every attack hit deals us this turn, from Tainted."""
    for status in gs.player.get("status", []) or []:
        m = _TAINTED_STATUS_RE.search(status.get("description") or status.get("text") or "")
        if m:
            return int(m.group(1))
        if "TAINTED" in (status.get("id") or status.get("name") or "").upper():
            return status.get("amount") or 0
    return 0


def _enemy_thorns(enemy: dict[str, Any]) -> int:
    """Damage this enemy deals back per attack that hits it."""
    for status in enemy.get("status", []):
        if "THORN" in (status.get("id") or status.get("name") or "").upper():
            return status.get("amount") or 0
    return 0


def _thorns_cost(card: dict[str, Any], enemy: dict[str, Any]) -> int:
    """HP a card costs us from Thorns. Charged *per hit*, so the Silent's
    multi-hit kit -- Shivs above all -- is the most expensive way to kill a
    Thorns enemy, which is exactly the kit the bot builds toward."""
    thorns = _enemy_thorns(enemy)
    if thorns <= 0 or not _is_attack_option(card):
        return 0
    # `_hit_count` already folds in the Shivs a card generates (Blade Dance
    # reports 3), so adding `_card_shiv_count` on top double-charged them.
    return thorns * _hit_count(card)


def _strength_down_amount(card: dict[str, Any]) -> int:
    m = _STRENGTH_DOWN_RE.search(card.get("description", "") or "")
    if not m:
        return 0
    raw = m.group(1)
    return 0 if raw.upper() == "X" else int(raw)


def _mitigation_value(card: dict[str, Any], enemies: list[dict[str, Any]]) -> int:
    """Damage a mitigation card actually prevents this turn.

    Strength reduction applies **per hit**, so Piercing Wail ("ALL enemies
    lose 6 Strength this turn") is worth 6 against a single attack and 18
    against a 3-hit one. The bot previously treated `_reduces_incoming_damage`
    as a yes/no flag and never computed the amount, so it could not tell
    whether a Wail fully covered the turn or barely dented it.
    """
    total = 0
    down = _strength_down_amount(card)
    hits_all = "all enem" in (card.get("description", "") or "").lower()
    for enemy in enemies:
        incoming = _enemy_attack_damage(enemy)
        if incoming <= 0:
            continue
        prevented = 0
        if down:
            prevented = down * _enemy_hit_count(enemy)
        if _card_applies_weak(card):
            prevented = max(prevented, int(incoming * WEAK_DAMAGE_REDUCTION))
        total += min(prevented, incoming)
        if down and not hits_all:
            break  # single-target strength reduction hits one enemy only
    return total


def _reduces_incoming_damage(card: dict[str, Any]) -> bool:
    """Cards that shrink the hit we're about to take -- Weak (attacks deal
    25% less) or enemy Strength reduction (Piercing Wail, Malaise).

    Order matters: blocking first and *then* weakening means the block was
    sized against the un-weakened number, so part of it is wasted. These have
    to be played before the block decision, not after.
    """
    description = card.get("description", "")
    if _card_applies_weak(card):
        return True
    # "Enemy loses X Strength" / "ALL enemies lose 6 Strength this turn"
    return bool(_STRENGTH_DOWN_RE.search(description) and "enem" in description.lower())


def _causes_discard(card: dict[str, Any]) -> bool:
    """Cards that discard from hand as part of their effect (Survivor,
    Acrobatics, Prepared, Storm of Steel, Calculated Gamble...)."""
    return bool(_DISCARDS_RE.search(card.get("description", "")))


def _sly_payoff_available(card: dict[str, Any], hand: list[dict[str, Any]]) -> bool:
    """True when playing `card` would let us discard a Sly card, playing that
    Sly card for free.

    This is the Silent's core engine: Survivor (block + discard 1) alongside a
    Sly attack is strictly better than a plain Defend, because the discard
    triggers the Sly card at no energy cost. Ranking block cards purely by
    block value misses it entirely.
    """
    if not _causes_discard(card):
        return False
    return any(
        card_db.is_sly(c) and c.get("index") != card.get("index") for c in hand
    )


def _scales_with_later_plays(card: dict[str, Any]) -> bool:
    """Cards whose payoff grows with every card played *after* them.

    Choke ("whenever you play a card this turn, the enemy loses N HP") and
    Strangle are the cases: played last they do almost nothing, played first
    they collect from the whole turn. They're Attacks, not Powers, so the
    Power-first rule doesn't catch them.
    """
    return bool(_SCALES_WITH_PLAYS_RE.search(card.get("description", "") or ""))


def _is_power(card: dict[str, Any]) -> bool:
    """Power cards. Almost all of them pay off per card played or per turn
    that *follows* them, so a Power played late is a Power half-wasted."""
    return (card.get("type") or "").lower() == "power"


def _is_scaling_buff(card: dict[str, Any]) -> bool:
    """Cards that should be played before whatever they scale.

    Two groups:
      * Powers -- Afterimage (Block per card played), Accuracy (Shivs hit
        harder), Envenom, Serpent Form, Infinite Blades... their value is
        proportional to how many cards follow them this turn and after.
      * Dexterity/Strength gains, which may be plain Skills (Anticipate).
        Strength raises Attack damage and Dexterity raises Block gained, so
        one sequenced after a Defend or Strike is pure waste that turn.

    A live sample found 50 of 73 turns holding a playable Power played
    something else first -- e.g. Backstab and Ricochet ahead of Accuracy,
    which exists to make exactly those Shivs hit harder.

    The 'Gain N Dexterity/Strength' match is deliberately narrow so Wraith
    Form ('...lose 1 Dexterity') and Malaise ('Enemy loses X Strength') don't
    qualify -- though Wraith Form still qualifies as a Power, which is right.
    """
    if _is_power(card):
        return True
    return bool(_GAIN_BUFF_RE.search(card.get("description", "")))


def _card_poison(card: dict[str, Any]) -> int:
    """Poison this card applies, including repeat multipliers.

    Bouncing Flask reads "Apply 3 Poison to a random enemy **3 times**" -- 9
    poison, not 3. Reading only the first number made it score below a plain
    6-damage Strike, so the bot kept picking the Strike over strictly more
    damage.
    """
    desc = card.get("description", "")
    m = _POISON_RE.search(desc)
    if not m:
        return 0
    total = int(m.group(1))
    return total * _repeat_multiplier(desc)


_DELAYED_TRIGGER_RE = re.compile(
    r"at the (?:start|end) of (?:your|each|the) turn"
    r"|whenever you"
    r"|every turn",
    re.IGNORECASE,
)


def _immediate_poison(card: dict[str, Any]) -> int:
    """Poison applied the moment we play the card -- nothing later.

    Noxious Fumes reads "At the START OF YOUR TURN, apply 2 Poison to ALL
    enemies": that poison does not exist until our *next* turn. `_card_poison`
    matched "apply 2 Poison" regardless, so a 1 HP enemy read as already dead
    and Noxious Fumes was selected as the *lethal* play -- and because the
    lethal step deliberately picks the smallest sufficient hit to avoid
    overkill, a Power with 0 direct damage sorted ahead of the Strike that
    would actually have killed it.

    Observed live: Snapping Jaxfruit left alive at 1 HP, Survivor and a Strike
    unused, 13 damage taken. Keeping `_card_poison` intact for scoring is
    deliberate -- Noxious Fumes really is worth its poison, just not this
    turn.
    """
    if _DELAYED_TRIGGER_RE.search(card.get("description", "") or ""):
        return 0
    return _card_poison(card)


def _repeat_multiplier(description: str) -> int:
    """"twice" / "N times" repeat count in a card's text (1 if absent)."""
    if re.search(r"\btwice\b", description, re.IGNORECASE):
        return 2
    m = _TIMES_RE.search(description)
    return int(m.group(1)) if m else 1


_CONDITIONAL_ON_STATUS_RE = re.compile(
    r"if the enemy has (\w+)", re.IGNORECASE
)


def _unmet_condition(card: dict[str, Any], enemies: list[dict[str, Any]]) -> bool:
    """True when a card's whole effect is gated on something not currently true.

    Bubble Bubble reads "If the enemy has Poison, apply 9 Poison" -- against an
    unpoisoned enemy it costs 1 energy and does nothing at all.
    """
    m = _CONDITIONAL_ON_STATUS_RE.search(card.get("description", "") or "")
    if not m:
        return False
    needed = m.group(1).upper()
    live = [e for e in enemies if e.get("hp", 0) > 0]
    if not live:
        return False
    return not any(
        needed in (st.get("id") or st.get("name") or "").upper()
        for e in live for st in (e.get("status") or [])
    )


_UTILITY_TEXT_RE = re.compile(r"\b(draw|energy|dexterity|strength|retain)\b", re.IGNORECASE)


def _is_pure_block(card: dict[str, Any]) -> bool:
    """Block and nothing else -- a plain Defend.

    The value step skips block cards when nothing needs blocking, which is
    right for a Defend and wrong for Backflip ("Gain 8 Block. Draw 2 cards").
    Treating any card with Block as a block card meant Backflip's draw was
    never valued at all: it reached play only through the leftover-energy
    step, ranked on pick rate. When the turn's damage is already covered, the
    Block is the part that does nothing and the draw is the whole point.
    """
    if _card_block(card) <= 0:
        return False
    description = card.get("description", "") or ""
    if _card_damage(card) or _card_poison(card):
        return False
    if _card_applies_weak(card) or _card_applies_vulnerable(card):
        return False
    return not _UTILITY_TEXT_RE.search(description)


_LASTING_BUFF_RE = re.compile(
    r"next turn|this turn, attacks|gain \d+ (?:strength|dexterity)"
    r"|attacks deal double",
    re.IGNORECASE,
)


def _grants_lasting_buff(card: dict[str, Any]) -> bool:
    """Does this card set something up that pays off beyond its own text?

    Used to stop cards being written off by their *cost*: Shadow Step discards
    the hand, but it is bought as a damage buff, not as a discard.
    """
    return bool(_LASTING_BUFF_RE.search(card.get("description", "") or ""))


def _is_dud_this_turn(card: dict[str, Any], gs: GameState) -> bool:
    """Cards that would do nothing useful if played right now.

    The game annotates conditional cards with their live value -- Knife Trap
    reads "Play every Shiv in your Exhaust Pile on the enemy. **(Plays 0
    Shivs)**" -- which is far more reliable than us inspecting piles. A live
    run played it with an empty exhaust pile for no effect.

    Calculated Gamble ("discard your Hand, then draw that many cards") is the
    other case: with nothing worth throwing away it burns a card and an
    Exhaust for no gain.
    """
    # A Tainted Skill (see `_tainted_gain`) costs its Tainted once per attack
    # hit coming this turn. When neither its own Block nor the Block already
    # up beyond the hit covers that, playing it only makes the hit worse. With
    # spare Block it is free: 23 Block against 19 absorbs Prepared's +2.
    gain = _tainted_gain(card)
    if gain:
        live = [e for e in gs.enemies if (e.get("hp") or 0) > 0]
        hits = _attack_hits(live)
        if hits:
            incoming_now = _incoming_damage(live) + _player_tainted(gs) * hits
            spare = gs.player.get("block", 0) + _pending_block_from_status(gs) - incoming_now
            if gain * hits - _card_block(card) > max(0, spare):
                return True

    # Damage written in terms of the board can resolve to nothing. Flechettes
    # ("Deal 5 damage for each Skill in your Hand") is worth exactly 0 with no
    # Skills left, and the sequencing step plays it *first* by design -- so
    # without this it burns an energy for no damage at the top of the turn.
    # Observed live.
    if _PER_SKILL_IN_HAND_RE.search(card.get("description", "") or ""):
        if not _contextual_damage(card, gs.hand):
            return True

    # The Block mirror of the Strength rule below. Lagavulin Matriarch -- the
    # single biggest killer of the current era -- stacks Dexterity down, and
    # at -4 a printed 5-Block Defend gives exactly nothing. The bot played
    # four of them in one fight for no effect, because only Strength was ever
    # checked. Written off only when Block is the card's whole purpose:
    # Survivor still discards, Backflip still draws, Leg Sweep still Weakens.
    dexterity = _player_dexterity(gs)
    if dexterity < 0 and _card_block(card) > 0 and _effective_block(card, dexterity) <= 0:
        description_l = (card.get("description", "") or "").lower()
        gives_else = (
            _card_damage(card) > 0
            or _card_poison(card) > 0
            or _card_shiv_count(card) > 0
            or _card_applies_weak(card)
            or _card_applies_vulnerable(card)
            or _DRAWS_CARDS_RE.search(description_l)
            or _GAINS_ENERGY_RE.search(description_l)
            or _forces_a_discard(card)
            or _is_power(card)
        )
        if not gives_else:
            return True

    # An attack that lands for nothing is not worth an energy. The game bakes
    # Strength into the text, so "Deal 0 damage" is simply what a fully
    # Strength-drained Strike looks like -- no arithmetic needed here. Only
    # written off when damage is the card's whole purpose: Neutralize still
    # applies its Weak at 0 damage, and poison ignores Strength entirely.
    # Keyed on the declared type, not `_is_attack_option` -- that helper is
    # itself damage-gated, so a Strike reduced to "Deal 0 damage" does not
    # register as an attack at all and would slip straight past this.
    if (card.get("type") or "").lower() == "attack" and not _card_shiv_count(card):
        description_l = (card.get("description", "") or "").lower()
        if (
            _card_damage(card) <= 0
            and _immediate_poison(card) <= 0
            and not _card_applies_weak(card)
            and not _card_applies_vulnerable(card)
            and not _card_block(card)
            and not _DRAWS_CARDS_RE.search(description_l)
            and not _GAINS_ENERGY_RE.search(description_l)
        ):
            return True

    description = card.get("description", "") or ""

    m = _PLAYS_N_RE.search(description)
    if m and int(m.group(1)) == 0:
        return True

    # Cards added to a full hand are discarded on arrival. Blade Dance ("Add 3
    # Shivs into your Hand. Exhaust.") is the sharp case: its entire value is
    # the Shivs, and at the hand cap they evaporate -- worse, it Exhausts, so
    # the card is spent permanently for nothing. Playing it frees its own slot
    # first, hence the -1.
    shivs = _card_shiv_count(card)
    if shivs:
        room = HAND_LIMIT - (len(gs.hand) - 1)
        if room <= 0:
            gains_else = (
                _card_damage(card) > 0
                or _card_block(card) > 0
                or _immediate_poison(card) > 0
                or _GAINS_ENERGY_RE.search(description)
            )
            if not gains_else:
                return True

    # Drawing into a full hand discards the drawn cards -- the draw is simply
    # lost. Only applies to cards whose value *is* the draw: Backflip still
    # gives Block and Adrenaline still gives Energy, so those stay playable.
    if len(gs.hand) >= HAND_LIMIT and _DRAWS_RE.search(description):
        gives_something_else = (
            _card_damage(card) > 0
            or _card_block(card) > 0
            or _card_poison(card) > 0
            or _card_shiv_count(card) > 0
            or _GAINS_ENERGY_RE.search(description)
        )
        if not gives_something_else:
            return True

    # The last hit of the fight, from an enemy already beaten (see
    # `_is_spent`): damage and poison do nothing, so a card whose only value is
    # damage is a dud. Anything that also blocks, weakens, draws or gives
    # energy keeps its place -- those can still change the hit.
    if _fight_ends_this_turn(gs) and _is_attack_option(card):
        still_useful = (
            _card_block(card) > 0
            or _card_applies_weak(card)
            or _DRAWS_RE.search(description)
            or _GAINS_ENERGY_RE.search(description)
        )
        if not still_useful:
            return True

    if _DISCARDS_HAND_RE.search(description):
        # Unless the discard is the payoff itself -- see `_discard_is_the_payoff`.
        if _discard_is_the_payoff(card, gs):
            return False
        # Whatever else it does, throwing the hand away while we still have
        # energy and cards to spend it on destroys the rest of the turn. Wait
        # until the energy is gone -- the discard costs nothing then, and the
        # buff lands just the same. Reported live: it discarded the hand
        # before playing down to 0 energy.
        spendable = [
            c for c in gs.hand
            if c.get("index") != card.get("index")
            and c.get("can_play") is not False
            and _cost_int(c) <= gs.energy
            and not _discards_whole_hand(c)
        ]
        if spendable:
            return True

        # ...otherwise, if the discard is the *cost* rather than the point,
        # it is worth playing. Shadow Step ("Discard your Hand. Next turn,
        # Attacks deal double damage.") is a 0-cost setup card whose whole
        # value is the buff; judging it by the discard wrote it off entirely,
        # so it sat unplayed as the only card in hand. Observed live, twice.
        if _grants_lasting_buff(card):
            return False
        # Only worth it if the hand actually holds cards we'd rather replace.
        others = [c for c in gs.hand if c.get("index") != card.get("index")]
        if not others:
            return True
        worth_keeping = sum(
            1
            for c in others
            if c.get("can_play", True) and (_is_attack_option(c) or _card_block(c) > 0)
        )
        # Nearly everything in hand is already useful -- don't gamble it away.
        return worth_keeping >= max(1, len(others) - 1)

    # A debuff aimed at an Artifact-shielded enemy is simply eaten ("Negates 2
    # debuffs"). If *every* live enemy is shielded there is nowhere useful to
    # aim it, so the card does nothing this turn -- play Expose first instead.
    if _unmet_condition(card, gs.enemies):
        return True

    # Weak only reduces *attack* damage, so a card whose ONLY effect is Weak
    # does nothing while nothing is winding up.
    #
    # The Block exemption matters and was missing: Leg Sweep reads "Apply 2
    # Weak. Gain 12 Block", so writing it off left the bot unable to play a
    # 12-Block card at all -- it stacked two Defends for 10 instead, and on a
    # turn where the enemy was merely buffing it played Backflip into 18 Block
    # against 0 incoming. Weak also lasts into the enemy's next turn, so
    # applying it while they wind up a buff is setup, not waste.
    if _card_applies_weak(card) and not _card_damage(card) and not _card_poison(card):
        live = [e for e in gs.enemies if e.get("hp", 0) > 0]
        if live and not any(_enemy_attack_damage(e) > 0 for e in live):
            return True

    # Intangible reduces all damage taken to 1, so a turn spent attacking into
    # it converts a whole hand into a couple of points. Soul Fysh (act 1 boss,
    # three kills in one set) uses it every few turns while escalating its own
    # damage 7 -> 24, so those turns are far better spent on Block, Powers, or
    # poison that ticks later. Only applies to cards whose *entire* value is
    # damage -- anything that also debuffs or blocks still earns its play.
    # Waking a sleeping enemy early throws away a free turn. Only applies when
    # the card's whole value is damage -- Powers, Block and setup still play,
    # which is exactly what those turns are for. A kill we can actually land
    # has already been taken by the lethal steps before this point.
    # Not on its last asleep turn, when it wakes anyway (the user's call): set
    # 4, run 3 spent that turn on Survivor and two Defends -- nothing was
    # coming -- with Strike+ and Strike in hand, and every recorded Matriarch
    # fight spent it on Block the same way.
    live = [e for e in gs.enemies if e.get("hp", 0) > 0]
    if (
        live
        and _is_attack_option(card)
        and all(_enemy_is_asleep(e) and not _wakes_anyway(e) for e in live)
        and not _card_block(card)
        and not _is_power(card)
    ):
        # ...unless the hand can actually land a real burst. Waking it for a
        # single Strike buys nothing -- 12 Plating absorbs it and the boss
        # starts swinging early -- but a hand holding a genuine payload is
        # worth spending the free turn on. A kill obviously qualifies.
        strength = _player_strength(gs)
        shiv = _shiv_damage_bonus(gs)
        playable = [c for c in gs.hand if c.get("can_play") is not False]
        burst = max(
            (_reachable_damage(playable, e, gs.energy, shiv) for e in live),
            default=0,
        )
        lethal = any(
            _kills_enemy(card, e, shiv, gs.hand, 0, strength,
                         _player_damage_multiplier(gs, e))
            for e in live
        )
        if burst < ASLEEP_WAKE_BURST and not lethal:
            return True

    if live and _is_attack_option(card) and all(_enemy_has_intangible(e) for e in live):
        if not _applies_debuff(card) and _card_block(card) <= 0:
            return True

    if _applies_debuff(card) and not _strips_artifact(card):
        live = [e for e in gs.enemies if e.get("hp", 0) > 0]
        if live and all(_debuff_is_wasted(card, e) for e in live):
            return True

    return False


def _is_attack_option(card: dict[str, Any]) -> bool:
    """Anything that contributes to killing an enemy -- direct damage, the
    shivs it generates (Blade Dance has 0 printed damage but hands over three
    4-damage shivs), or poison it applies."""
    return _card_damage(card) > 0 or _card_shiv_count(card) > 0 or _card_poison(card) > 0


def _hit_count(card: dict[str, Any]) -> int:
    """How many separate damage instances this card produces.

    Matters against Intangible, where every hit is reduced to 1 regardless of
    size: three 4-damage shivs do 3, while an 11-damage Backstab does 1. Under
    Intangible the right play is to dump cheap multi-hit cards and hold the
    big attacks until it wears off.
    """
    description = card.get("description", "")
    hits = 1 if _DAMAGE_RE.search(description) else 0
    if hits:
        if re.search(r"\btwice\b", description, re.IGNORECASE):
            hits *= 2
        else:
            m = _TIMES_RE.search(description)
            if m:
                hits *= int(m.group(1))
    return hits + _card_shiv_count(card)


def _enemy_is_asleep(enemy: dict[str, Any]) -> bool:
    """Lagavulin Matriarch opens "Asleep -- awakens upon losing HP or after 3
    turns", doing nothing until then.

    Those are free turns, and *any* damage spends them early. It wakes on its
    own regardless, so the turns are only worth what we put into them --
    Powers, poison engines, Dexterity, Block -- and chip damage buys nothing:
    it carries 12 Plating a turn, so a Strike is absorbed anyway while waking
    a 222 HP boss that hits for 23.

    It is the single biggest killer of the current era (6 of the last 40 runs)
    and none of this was modelled.
    """
    for status in enemy.get("status", []) or []:
        name = (status.get("id") or status.get("name") or "").upper()
        text = (status.get("text") or status.get("description") or "").lower()
        if "ASLEEP" in name or "awakens upon losing hp" in text:
            return True
    return False


def _wakes_anyway(enemy: dict[str, Any]) -> bool:
    """On its last asleep turn ("Asleep 1") the enemy wakes whatever we do,
    so not hitting it protects nothing."""
    for status in enemy.get("status", []) or []:
        if "ASLEEP" in (status.get("id") or status.get("name") or "").upper():
            return (status.get("amount") or 0) <= 1
    return False


# Reattach (each Decimillipede segment): "If other segments are still alive,
# revives in 2 turns with 25 HP." A segment killed far ahead of the others is
# undone -- it is back with 25 HP before they can follow -- so the fight is won
# only by bringing the segments down together. Set 3, run 13 died to one; the
# evidence is that single fight, so this is grade C.
_REATTACH_RE = re.compile(r"revives in \d+ turns? with (\d+) HP", re.IGNORECASE)


def _reattach_hp(enemy: dict[str, Any]) -> int:
    """HP this enemy comes back with if killed while others live, or 0."""
    for status in enemy.get("status", []) or []:
        m = _REATTACH_RE.search(status.get("description") or status.get("text") or "")
        if m:
            return int(m.group(1))
    return 0


def _kill_is_undone(enemy: dict[str, Any], enemies: list[dict[str, Any]]) -> bool:
    """Killing this segment now only brings it back later: another segment is
    still too healthy to fall before it revives."""
    revive = _reattach_hp(enemy)
    if not revive:
        return False
    others = [
        e for e in enemies
        if e.get("entity_id") != enemy.get("entity_id") and (e.get("hp") or 0) > 0 and _reattach_hp(e)
    ]
    return any((e.get("hp") or 0) > revive for e in others)


def _enemy_has_intangible(enemy: dict[str, Any]) -> bool:
    return any(
        "INTANGIBLE" in (s.get("id") or s.get("name") or "").upper()
        for s in enemy.get("status", [])
    )


def _enemy_has_vulnerable(enemy: dict[str, Any]) -> bool:
    return any("VULNERABLE" in (s.get("id") or s.get("name") or "").upper() for s in enemy.get("status", []))


def _enemy_has_weak(enemy: dict[str, Any]) -> bool:
    """Needed by conditional damage doublers -- Tracking reads "Weak enemies
    take double damage from Attacks", which is only true of a Weak target."""
    return any("WEAK" in (s.get("id") or s.get("name") or "").upper()
               for s in enemy.get("status", []))


def _enemy_poison(enemy: dict[str, Any]) -> int:
    for s in enemy.get("status", []):
        name = (s.get("id") or s.get("name") or "").upper()
        if "POISON" in name:
            return s.get("amount") or 0
    return 0


_BLOCK_REGEN_RE = re.compile(
    r"gain (\d+) block", re.IGNORECASE
)


def _enemy_block_regen(enemy: dict[str, Any]) -> int:
    """Block this enemy hands itself every turn, e.g. Plating.

    Only the *player's* Plating was ever read. Lagavulin Matriarch carries
    "At the end of your turn, gain 12 Block" on 222 HP, so a deck chipping for
    less than 12 a turn makes no progress at all while taking 23 a turn back.
    It is the single biggest killer of the current era, and the bot could not
    see the wall.

    Poison is unaffected -- it bypasses Block entirely -- which is exactly why
    it is the answer to this fight.
    """
    total = 0
    for status in enemy.get("status", []) or []:
        text = (status.get("text") or status.get("description") or "")
        name = (status.get("id") or status.get("name") or "").upper()
        if "PLATING" in name or "METALLICIZE" in name or _BLOCK_REGEN_RE.search(text):
            m = _BLOCK_REGEN_RE.search(text)
            amount = int(m.group(1)) if m else (status.get("amount") or 0)
            total += int(amount or 0)
    return total


def _effective_hp(enemy: dict[str, Any]) -> int:
    """HP that still needs *our* damage -- Poison already ticking down counts,
    since it kills the enemy before their next turn regardless of what we do.

    Deliberately excludes enemy Block: poison bypasses block, so this is the
    right number for "will poison alone finish it?".
    """
    return max(0, enemy.get("hp", 0) - _enemy_poison(enemy))


_STUN_THRESHOLD_RE = re.compile(r"hp\s+reaches\s+(\d+)\s+or\s+below", re.IGNORECASE)


def _stun_threshold(enemy: dict[str, Any]) -> Optional[int]:
    """HP at or below which this enemy becomes Stunned, or None.

    Read from the status *description* rather than a name list, so any enemy
    carrying the mechanic counts. Terror Eel's `Shriek` reads "The first time
    Terror Eel's HP reaches 70 or below, it becomes Stunned", and Stun is
    "Prevent the enemy from acting on its next turn." The status is only
    present until it fires, so seeing it at all means the line has not been
    crossed yet.
    """
    for status in enemy.get("status", []):
        text = status.get("description") or ""
        if "stun" not in text.lower():
            continue
        m = _STUN_THRESHOLD_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def _damage_to_stun(enemy: dict[str, Any]) -> Optional[int]:
    """Attack damage needed this turn to trip the stun, Block included."""
    threshold = _stun_threshold(enemy)
    if threshold is None:
        return None
    needed = _effective_hp(enemy) - threshold
    if needed <= 0:
        return None  # already at or below the line
    return _enemy_block(enemy) + needed


# Skittish: "The first time Phantasmal Gardener is hit each turn, it gains 6
# Block." Three Gardener elites wrecked set-3 runs (2, 7, 9). The Block almost
# never shows in the payload -- in 178 logged hits on a Skittish enemy with no
# Block, the next payload showed it 17 times -- so the bot counts it from the
# hits its own cards sent this turn. Run 2, floor 14: two Strikes planned into
# a Gardener on 7; the first raised 6 Block, the second was absorbed, and 28
# came through with a Defend still in hand.
_SKITTISH_RE = re.compile(r"first time .+? is hit each turn, it gains (\d+) Block", re.IGNORECASE)
# Enemies our own damage cards were sent into this turn: entity_id ->
# {"count": cards sent, "after_first": damage sent after the first hit}.
_turn_hits: dict[str, Any] = {"key": None, "hits": {}}


def reset_turn_memory() -> None:
    _turn_hits["key"] = None
    _turn_hits["hits"] = {}


def _skittish_amount(enemy: dict[str, Any]) -> int:
    for status in enemy.get("status") or []:
        m = _SKITTISH_RE.search(status.get("description") or "")
        if m:
            return int(m.group(1))
    return 0


def _hidden_block(enemy: dict[str, Any]) -> int:
    """Skittish Block a hit this turn raised that the payload does not show."""
    amount = _skittish_amount(enemy)
    if not amount or (enemy.get("block") or 0) > 0:
        return 0
    rec = _turn_hits["hits"].get(enemy.get("entity_id"))
    if not rec:
        return 0
    return max(0, amount - rec["after_first"])


def _skittish_pending(enemy: dict[str, Any]) -> int:
    """Skittish Block not raised yet this turn: the first hit lands in full,
    every hit after it meets this much Block."""
    amount = _skittish_amount(enemy)
    if not amount or (enemy.get("block") or 0) > 0:
        return 0
    return 0 if enemy.get("entity_id") in _turn_hits["hits"] else amount


def _note_hits(gs: GameState, action: tuple[str, dict[str, Any]]) -> None:
    """Remember which enemies a damage card was just sent into (for Skittish).

    Random-target damage marks every enemy as hit but credits none of it
    against the Block -- where it landed is unknown, so the Block is assumed
    to still be up.
    """
    key = (gs.act, gs.floor, _battle_round(gs))
    if _turn_hits["key"] != key:
        _turn_hits["key"] = key
        _turn_hits["hits"] = {}
    kind, fields = action
    if kind != "play_card":
        return
    card = next((c for c in gs.hand if c.get("index") == fields.get("card_index")), None)
    damage = _card_damage(card) if card else 0
    if damage <= 0:
        return
    target = fields.get("target")
    live = [e for e in gs.enemies if (e.get("hp") or 0) > 0]
    struck = [e for e in live if e.get("entity_id") == target] if target else live
    random_target = bool(_RANDOM_TARGET_RE.search(card.get("description") or ""))
    per_hit = damage / max(1, _hit_count(card) - _card_shiv_count(card))
    for enemy in struck:
        rec = _turn_hits["hits"].setdefault(enemy.get("entity_id"), {"count": 0, "after_first": 0})
        if not random_target:
            rec["after_first"] += damage if rec["count"] else damage - per_hit
        rec["count"] += 1


def _enemy_block(enemy: dict[str, Any]) -> int:
    return (enemy.get("block", 0) or 0) + _hidden_block(enemy)


def _damage_to_kill(enemy: dict[str, Any]) -> int:
    """Attack damage needed to actually kill this enemy, i.e. through its
    Block.

    Targeting on raw HP alone sent attacks into the lowest-HP enemy even when
    a wall of Block meant they were entirely absorbed, while an unblocked
    enemy stood next to it.
    """
    return _enemy_block(enemy) + _effective_hp(enemy)


_DAMAGE_CAP_RE = re.compile(
    r"cannot lose more than\s+(\d+)\s+hp\s+each\s+turn", re.IGNORECASE
)


def _damage_allowance(enemy: dict[str, Any]) -> Optional[int]:
    """HP this enemy can still lose this turn, or None if uncapped.

    Skulking Colony's `Hardened Shell` reads "Skulking Colony cannot lose more
    than 20 HP each turn". The status `amount` is the *remaining* allowance,
    not the cap -- it was logged counting down 20 -> 17 -> 14 -> 11 within a
    single turn as damage landed. Everything above it is thrown away, so the
    bot must stop pouring damage in and spend the rest of the turn elsewhere.
    """
    for status in enemy.get("status", []):
        text = status.get("description") or ""
        if _DAMAGE_CAP_RE.search(text):
            amount = status.get("amount")
            return int(amount) if amount is not None else 0
    return None


def _capped_damage(enemy: dict[str, Any], damage: float) -> float:
    """Damage that will actually land, after any per-turn HP-loss cap."""
    allowance = _damage_allowance(enemy)
    return damage if allowance is None else min(damage, float(allowance))


_RANDOM_TARGET_RE = re.compile(r"random enemy", re.IGNORECASE)


def _certain_on(card: dict[str, Any], enemies: Optional[list[dict[str, Any]]]) -> bool:
    """Whether all of this card's damage is sure to land on the enemy we mean.

    Ricochet reads "Deal 3 damage to a random enemy 4 times". Counted as 12
    into one Cultist at 13 HP, Strike + Ricochet "killed" it, so the bot skipped
    blocking 31 -- the Cultist took 3 of Ricochet's 12, survived on 4, and the
    bot fell from 56 to 25. The same rule as `_per_play_bonus_vs`: random only
    counts toward a specific kill when one enemy is left. `enemies=None` keeps
    the old behaviour for callers that have no board to check.
    """
    if enemies is None or not _RANDOM_TARGET_RE.search(card.get("description") or ""):
        return True
    return len([e for e in enemies if e.get("hp", 0) > 0]) <= 1


def _kills_enemy(
    card: dict[str, Any],
    enemy: dict[str, Any],
    shiv_bonus: int,
    hand: Optional[list[dict[str, Any]]] = None,
    per_play: int = 0,
    strength: int = 0,
    multiplier: float = 1.0,
    live_enemies: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Whether this single card finishes the enemy.

    Two routes, because Poison ignores Block: either the poison we apply
    covers its post-poison HP, or our attack damage covers Block + that HP.
    """
    needed = _damage_to_kill(enemy)
    if not _certain_on(card, live_enemies):
        return per_play >= needed  # the card's own hits may land elsewhere
    allowance = _damage_allowance(enemy)
    if allowance is not None and needed > allowance:
        # A per-turn HP-loss cap makes the kill impossible this turn no matter
        # what we play, so claiming it would waste the whole turn's damage.
        return False
    if _immediate_poison(card) >= _effective_hp(enemy) > 0:
        return True
    # `per_play` is damage a power deals just for playing a card (Serpent
    # Form), so it lands whatever the card is -- including a Defend.
    damage = _effective_damage(card, enemy, shiv_bonus, hand, strength, multiplier) + per_play
    # Skittish not raised yet: the first hit lands in full, the rest meet the
    # Block it raises -- see `_skittish_pending`.
    hits = _hit_count(card)
    pending = _skittish_pending(enemy)
    if pending and hits > 1 and damage / hits < needed:
        needed += pending
    return damage >= needed


def _dies_despite_kill(
    card: dict[str, Any], enemy: dict[str, Any], ctx: "_TurnContext", hp: int
) -> bool:
    """Whether what still gets through after this card kills `enemy` is lethal.

    Only the target's own attack goes away with it, plus whatever Block the
    killing card itself gives (Dash). An enemy that is not attacking takes
    nothing off the hit when it dies.
    """
    residual = ctx.unblocked - _enemy_attack_damage(enemy) - _effective_block(card, ctx.dexterity)
    return max(0, residual) + ctx.hp_loss >= hp


def _self_damage_in_hand(card: dict[str, Any]) -> int:
    """Damage this card does to us just for sitting in hand."""
    if (card.get("type") or "").lower() not in ("curse", "status"):
        return 0
    m = _SELF_DAMAGE_RE.search(card.get("description", "") or "")
    return int(m.group(1)) if m else 0


_HP_LOSS_RE = re.compile(r"lose\s+(\d+)\s+HP", re.IGNORECASE)


def _unblockable_self_damage(card: dict[str, Any]) -> int:
    """The portion of a held card's self-damage that Block cannot stop.

    "Lose N HP" bypasses Block entirely, while "take N damage" does not. The
    two read almost identically -- Infection says "take 3 damage", Beckon says
    "lose 6 HP" -- so they were being summed together and answered with
    Defend. Against Soul Fysh, which hands out 1-2 Beckons every turn, that
    meant stacking Block against damage Block could never touch. Three runs
    died to it in one set.
    """
    if (card.get("type") or "").lower() not in ("curse", "status"):
        return 0
    m = _HP_LOSS_RE.search(card.get("description", "") or "")
    return int(m.group(1)) if m else 0


def _hand_hp_loss(hand: list[dict[str, Any]]) -> int:
    """Unblockable end-of-turn HP loss from cards sitting in hand."""
    return sum(_unblockable_self_damage(c) for c in hand)


def _hand_curse_damage(hand: list[dict[str, Any]]) -> int:
    """Cards that hurt us just for being in hand (e.g. "at the end of your
    turn, if this is in your Hand, take 3 damage").

    Must cover **Status as well as Curse**: Infection is a *Status*, and a
    Curse-only check made its damage invisible to the block decision. Across
    10 recorded runs the bot accumulated 41 Infections, each worth 3 damage a
    turn while held -- a serious, entirely unaccounted drain (runs averaged 98
    damage taken).

    These are Unplayable, so they never appear in the playable-hand filter;
    check the whole hand. Treated as block-preventable like an enemy attack:
    if that's wrong for some specific card the cost is a little overflow
    block, not a missed threat.
    """
    return sum(_self_damage_in_hand(c) for c in hand)


_PER_PLAY_DAMAGE_RE = re.compile(
    r"whenever you play a card,?\s*deal\s+(\d+)\s+damage", re.IGNORECASE
)


def _per_play_damage(gs: GameState) -> int:
    """Damage our own powers deal each time we play *any* card.

    Serpent Form is the case: the live status reads "Whenever you play a card,
    deal 6 damage to a random enemy". Nothing read it, so with Serpent Form up
    and an enemy on 6 HP the bot could not see that playing any card at all
    would kill it -- `_kills_enemy` counted only the card's printed damage.

    Read from the status description so an equivalent power counts too, with
    the live `amount` preferred since the upgraded card deals more.
    """
    for status in gs.player.get("status", []):
        description = status.get("description") or ""
        m = _PER_PLAY_DAMAGE_RE.search(description)
        if m:
            return status.get("amount") or int(m.group(1))
    return 0


def _per_play_bonus_vs(gs: GameState, enemies: list[dict[str, Any]]) -> int:
    """The above, but only when it is *certain* to hit the enemy we mean.

    The effect picks a random enemy, so with more than one alive it cannot be
    counted toward a specific kill. With exactly one, random is deterministic.
    """
    live = [e for e in enemies if e.get("hp", 0) > 0]
    return _per_play_damage(gs) if len(live) == 1 else 0


def _shiv_damage_bonus(gs: GameState) -> int:
    """Accuracy ('Shivs deal 4 additional damage') magnitude, if the power is
    active -- read its live amount rather than hardcoding the base value."""
    for status in gs.player.get("status", []):
        name = (status.get("id") or status.get("name") or "").upper()
        if "ACCURACY" in name:
            return status.get("amount") or 4
    return 0


def _effective_damage(
    card: dict[str, Any],
    enemy: dict[str, Any],
    shiv_bonus: int,
    hand: Optional[list[dict[str, Any]]] = None,
    strength: int = 0,
    multiplier: float = 1.0,
) -> float:
    """Damage this card ultimately removes from `enemy`.

    Attack damage (its own hit plus the free shivs it generates) is scaled by
    Vulnerable already on the target -- not by Vulnerable the card is only now
    applying, since that only helps later plays. Poison is added *unscaled*:
    Vulnerable boosts attacks, not poison, but the poison still kills, so it
    counts toward lethal.
    """
    # Intangible caps every hit at 1, so size stops mattering and only the
    # number of hits does -- three shivs beat one big attack.
    if _enemy_has_intangible(enemy):
        return float(_hit_count(card) + (1 if _immediate_poison(card) else 0))

    mult = VULNERABLE_MULTIPLIER if _enemy_has_vulnerable(enemy) else 1.0
    direct = _card_damage(card)
    if hand is not None:
        contextual = _contextual_damage(card, hand)
        if contextual is not None:
            direct = contextual
    # NOTE: Strength is deliberately NOT applied to `direct`. The game already
    # bakes it into the card text -- at Strength -2 a Strike reports "Deal 4
    # damage", at -4 "Deal 2 damage" -- so adding it again double-counts and
    # badly under-reads our own damage. Verified against live payloads.
    #
    # Shivs are the exception: they are *generated* rather than printed, so
    # their damage is not in any card text and Strength must be applied here.
    shiv_total = _card_shiv_count(card) * max(0, SHIV_BASE_DAMAGE + shiv_bonus + strength)
    # Poison is added after the multiplier: damage doublers boost hits, not
    # poison ticks.
    return (direct + shiv_total) * mult * multiplier + _immediate_poison(card)


def _poison_total(stack: int, turns: float) -> int:
    """Total HP a poison application removes over the rest of the fight.

    Poison ticks at the start of the enemy's turn and drops by 1 each time,
    so applying 5 deals 5+4+3+2+1 = 15 if the fight lasts five more turns --
    triangular, not flat. Counting only the raw stack made Deadly Poison (5)
    look weaker than a Strike (6) when it is worth far more in any fight that
    lasts, which is exactly the elite fights that were killing us.
    """
    if stack <= 0 or turns <= 0:
        return 0
    ticks = int(min(stack, turns))
    # Sum of stack, stack-1, ... over `ticks` turns.
    return ticks * stack - (ticks * (ticks - 1)) // 2


def _expected_turns_to_kill(
    enemies: list[dict[str, Any]], hand: list[dict[str, Any]], shiv_bonus: int
) -> float:
    """Rough number of turns this fight still has left, from our damage output.

    Used to value effects that pay out over time; poison in a fight that ends
    next turn is worth almost nothing, and worth a great deal in a grind.
    """
    if not enemies:
        return 0.0
    output = sum(_effective_damage(c, enemies[0], shiv_bonus, hand) for c in hand)
    if output <= 0:
        return 1.0
    remaining = sum(_damage_to_kill(e) for e in enemies)
    return max(1.0, min(remaining / output, POISON_MAX_HORIZON))


_HITS_ALL_RE = re.compile(r"to ALL enemies", re.IGNORECASE)
_SHIVS_HIT_ALL_RE = re.compile(r"shivs?\s+now hit ALL", re.IGNORECASE)


def _shivs_hit_all(gs: GameState) -> bool:
    """True when a Power has made Shivs strike every enemy (Fan of Knives)."""
    for status in gs.player.get("status", []):
        text = status.get("description") or ""
        name = (status.get("id") or status.get("name") or "").upper()
        if _SHIVS_HIT_ALL_RE.search(text) or "FAN_OF_KNIVES" in name or name == "FAN OF KNIVES":
            return True
    return False


def _spread_multiplier(card: dict[str, Any], gs: GameState, enemies: list[dict[str, Any]]) -> int:
    """How many enemies this card's damage actually lands on.

    A card reading "to ALL enemies" is worth its damage once per enemy, and a
    Shiv becomes one of those once Fan of Knives is out ("Shivs now hit ALL
    enemies"). Valuing either at single-target damage undersells them by a
    factor of the enemy count -- which is the whole reason to play them.

    Ranking only. Lethal against one specific enemy still uses single-target
    damage, because that is what actually lands on *that* enemy.
    """
    # Count only enemies our damage still has to deal with. One already doomed
    # by ticking poison dies before it acts, so spreading damage onto it is
    # worth nothing -- counting it would inflate an AoE card's value with
    # targets that never needed hitting.
    live = max(1, len([e for e in enemies if _effective_hp(e) > 0]))
    if live == 1:
        return 1
    description = card.get("description", "") or ""
    if _HITS_ALL_RE.search(description):
        return live
    if _shivs_hit_all(gs) and (
        "SHIV" in (card.get("name") or "").upper() or _card_shiv_count(card) > 0
    ):
        return live
    return 1


def _ranking_damage(
    card: dict[str, Any],
    enemy: dict[str, Any],
    shiv_bonus: int,
    hand: Optional[list[dict[str, Any]]],
    turns: float,
    gs: Optional[GameState] = None,
) -> float:
    """Damage used to *rank* attacks, crediting poison over the fight's life.

    Kept separate from `_effective_damage`, which stays a strict
    this-instant figure so the lethal check can't be fooled into thinking a
    poison application kills something right now.
    """
    poison = _card_poison(card)
    base = _effective_damage(card, enemy, shiv_bonus, hand) - poison
    if gs is not None:
        base *= _spread_multiplier(card, gs, gs.enemies)
    # Damage beyond a per-turn HP-loss cap is discarded by the game, so it
    # must not make an oversized attack look better than a cheap one.
    landed = _capped_damage(enemy, base) + _poison_total(poison, turns)
    # Thorns bills us per hit, so a five-Shiv turn into 2 Thorns costs 10 HP.
    # Charging it here is what stops the multi-hit kit -- the Silent's whole
    # game plan -- being the default answer to a Thorns enemy.
    return landed - _thorns_cost(card, enemy)


def _needs_target(card: dict[str, Any]) -> bool:
    return card.get("target_type") not in ("Self", "AllEnemies", "None", None)


def _intent_damage(label: str) -> int:
    """Damage from one attack intent label.

    Labels are either a plain number ("8") or a multi-hit "NxM" ("4x4" = four
    hits of 4 = 16). Reading only the first number -- as this did --
    under-counted 16% of all attack intents in a 10-run sample, some by 4x,
    which is chronic under-blocking in exactly the dangerous fights.

    Enemy debuffs are already applied by the game: a Weakened enemy reports
    '3' where it would otherwise report '4' (verified against logged intents),
    so we must NOT scale these again.
    """
    if not label:
        return 0
    m = _INTENT_MULTI_RE.search(label)
    if m:
        return int(m.group(1)) * int(m.group(2))
    m = _INTENT_NUM_RE.search(label)
    return int(m.group(1)) if m else 0


def _incoming_damage(enemies: list[dict[str, Any]]) -> int:
    """Total telegraphed attack damage from these enemies.

    Delegates to `_enemy_attack_damage`, which is where the intent rules live:
    the denylist of harmless types, the prose fallback, Back Attack. This used
    to carry its own copy -- `type == "Attack"` -- so the DeathBlow fix and the
    Back Attack fix both landed in `_enemy_attack_damage` and never reached the
    survival math, which reads *this*. Across 19,445 logged turns the two
    disagreed on 305: 135 death blows read as 0 (Waterfall Giant's final 51
    among them -- a turn the bot died holding a potion it never drank) and
    170 back attacks read at two-thirds.
    """
    return sum(_enemy_attack_damage(e) for e in enemies)


def _should_race(
    gs: GameState,
    enemies: list[dict[str, Any]],
    hand: list[dict[str, Any]],
    shiv_bonus: int,
) -> bool:
    """Whether to trade HP for tempo rather than turtle.

    Turtling is a trap in a long fight: damage taken scales with how many
    turns it lasts, so spending every turn blocking can cost more HP in total
    than eating some hits and killing sooner. Enabled only when all of:

      * it's an elite/boss (trash fights end quickly anyway),
      * the fight is genuinely long -- more than `RACE_MIN_TURNS` turns of
        damage still to grind through, and
      * we can afford it: HP comfortably above the safety floor.

    The last condition matters -- racing while nearly dead is just dying
    faster, so the safety floor and the about-to-die checks still bind.
    """
    if gs.state_type not in ("elite", "boss"):
        return False
    if not enemies:
        return False

    output = sum(_effective_damage(c, enemies[0], shiv_bonus, hand) for c in hand)
    # An enemy that re-blocks every turn eats that much of our output before
    # any of it touches its HP. Ignoring it made the bot commit to races it
    # could never finish against Plating bosses.
    regen = sum(_enemy_block_regen(e) for e in enemies)
    poison_output = sum(_card_poison(c) for c in hand)
    output = max(poison_output, output - regen)
    if output <= 0:
        return False
    remaining = sum(_damage_to_kill(e) for e in enemies)
    turns_left = remaining / output
    if turns_left < RACE_MIN_TURNS:
        return False

    safety_floor = max(SAFETY_HP_ABS_FLOOR, SAFETY_HP_PCT_FLOOR * gs.max_hp)
    return gs.hp >= safety_floor * RACE_HP_SAFETY_MARGIN


# Two or more dead cards in hand is a real clog, not bad luck. Wrigglers add
# an Infection every other turn and Soul Fysh hands out 1-2 Beckons a turn; at
# the decisive logged turn the bot held four Infections and one Strike -- it
# did not choose not to block, it had nothing to block with.
CLOG_THRESHOLD = 2

# "Discard 1 card", "Discard your hand", "Exhaust a card in your hand" -- an
# outlet that removes *other* cards. A bare trailing "Exhaust." is the card
# exhausting itself and clears nothing, so it must not match.
_CLEARS_HAND_RE = re.compile(
    r"discard your hand"
    r"|discard \d+ card"
    r"|discard a card"
    r"|exhaust \d+ card"
    r"|exhaust a card",
    re.IGNORECASE,
)


def _dead_cards_in_hand(
    hand: list[dict[str, Any]], enemies: list[dict[str, Any]] | None = None
) -> int:
    """Status and Curse cards taking up hand slots and doing nothing for us.

    Frantic Escape is a *Status* card and is the only answer to the Act 2
    boss's death clock, so it must never be counted as dead weight -- without
    this the clog rule would rank it discardable and pitch the one card that
    saves the run.
    """
    clock = _death_clock(enemies or [])
    clock_name = clock[0] if clock else ""
    return sum(
        1 for c in hand
        if (c.get("type") or "").lower() in ("status", "curse")
        and not _delays_death_clock(c, clock_name)
    )


def _clears_clog(card: dict[str, Any]) -> bool:
    """Does this card remove other cards from our hand?"""
    return bool(_CLEARS_HAND_RE.search(card.get("description", "") or ""))


def _loses_value_if_deferred(
    card: dict[str, Any], hand: list[dict[str, Any]], clogged: bool
) -> bool:
    """Cards that are worth strictly less the longer we wait to play them.

    Four separate bugs turned out to be this one thing. `_play_timing` ranked
    each of these correctly, but an earlier step in `decide()` -- free damage,
    or block -- committed the turn before ordering was ever consulted:

    * **Choke** collects from every card played *after* it, so dumping a free
      Shiv first throws the collection away.
    * **Flechettes** counts the Skills in hand, and most block cards are
      Skills, so blocking first shrinks it by 5 a card.
    * **A clog outlet** costs us 3 HP a turn for every Infection it has not
      cleared yet.

    Rather than a fifth bespoke guard in a fifth step, they share one rule and
    one affordability check.
    """
    desc = card.get("description", "") or ""
    if _PER_SKILL_IN_HAND_RE.search(desc):
        return True
    if _scales_with_later_plays(card):
        return True
    if clogged and _clears_clog(card):
        return True
    # A discard outlet with a Sly card in hand: discarding a Sly card triggers
    # it for FREE, so the outlet has to go first. Defer it and the block step
    # simply plays the Sly card for energy instead -- observed live with
    # Prepared+ (0 cost, "Draw 1 card. Discard 1 card.") and Untouchable
    # ("Sly. Gain 7 Block") in the same hand, 14 unblocked: it paid for the
    # Block it could have had for nothing.
    if _forces_a_discard(card) and any(
        card_db.is_sly(c) and c.get("index") != card.get("index") for c in hand
    ):
        return True
    return False


# X-cost cards spend all remaining energy, and their text is written in terms
# of that X: Malaise+ reads "Enemy loses X+1 Strength. Apply X+1 Weak."
# `_cost_int` fell back to 0 for "X", so these looked free, and every text
# parser (damage, Weak, Strength, poison) saw a literal "X" and scored 0 --
# the card was invisible to the bot's reasoning rather than merely mispriced.
#
# Resolving X into concrete numbers up front means the existing parsers all
# work unchanged: `_mitigation_value` already models Strength reduction per
# hit and Weak as a percentage, it just never had usable numbers.
_X_TERM_RE = re.compile(r"(?<![A-Za-z])X(?:\s*\+\s*(\d+))?(?![A-Za-z])")


def _is_x_cost(card: dict[str, Any]) -> bool:
    return str(card.get("cost", "")).strip().upper() == "X"


def _resolve_x_card(card: dict[str, Any], energy: int) -> dict[str, Any]:
    """A copy of the card with X replaced by what it will actually be.

    "X+1" with 1 energy becomes "2". The cost is set to the energy the card
    will really consume, so affordability and sequencing stop treating it as
    free. Non-X cards are returned untouched.
    """
    if not _is_x_cost(card):
        return card
    resolved = dict(card)
    text = card.get("description", "") or ""

    def _sub(match: "re.Match[str]") -> str:
        bonus = int(match.group(1)) if match.group(1) else 0
        return str(max(0, energy + bonus))

    resolved["description"] = _X_TERM_RE.sub(_sub, text)
    resolved["cost"] = str(energy)
    resolved["_x_resolved"] = energy
    return resolved


def _playable_hand(gs: GameState) -> list[dict[str, Any]]:
    """Cards we can actually play right now.

    `can_play` is authoritative and must win over our own cost arithmetic:
    costs change mid-turn (Pounce makes the next Skill free, Bullet Time makes
    the whole hand free, Master Planner, cost-reduction effects), and the
    printed cost then no longer reflects what we'd pay. Requiring *both*
    can_play and `cost <= energy` meant a card the game said was playable got
    filtered out and stranded in hand.
    """
    playable: list[dict[str, Any]] = []
    for card in gs.hand:
        can_play = card.get("can_play")
        if can_play is False:
            continue
        # Legal but pointless right now (Knife Trap with no Shivs banked,
        # Calculated Gamble with a hand worth keeping). The game will happily
        # let us play these for no effect.
        if _is_dud_this_turn(card, gs):
            continue
        if can_play is True:
            playable.append(card)
            continue
        # Field absent (synthetic/partial payloads) -- fall back to cost math.
        if _cost_int(card) <= gs.energy:
            playable.append(card)
    return [_resolve_x_card(c, gs.energy) for c in playable]


def _cost_int(card: dict[str, Any]) -> int:
    cost = card.get("cost", 0)
    try:
        return int(cost)
    except (TypeError, ValueError):
        return 0  # "X" cost cards -- treat as free to consider, energy check still applies elsewhere


def _lowest_hp_enemy(enemies: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The easiest enemy to actually finish -- Block included, since damage
    into a blocked enemy is absorbed rather than killing anything."""
    if not enemies:
        return None
    return min(enemies, key=_damage_to_kill)


def _reachable_damage(
    hand: list[dict[str, Any]],
    enemy: dict[str, Any],
    energy: int,
    shiv_bonus: int,
    live_enemies: Optional[list[dict[str, Any]]] = None,
) -> float:
    """Total damage we could land on `enemy` this turn across *multiple* cards,
    within our energy budget.

    The single-card lethal check misses the common case where two Strikes
    would kill an attacker: no one card qualifies, so the bot fell through to
    blocking a hit it could have deleted entirely. Greedy by damage-per-energy
    (free cards first), which is close enough for a kill/no-kill judgement.
    """
    # Random-target damage cannot be promised to this enemy -- see `_certain_on`.
    attacks = [c for c in hand if _is_attack_option(c) and _certain_on(c, live_enemies)]
    if not attacks:
        return 0.0

    def _efficiency(card: dict[str, Any]) -> tuple[int, float]:
        cost = _cost_int(card)
        damage = _effective_damage(card, enemy, shiv_bonus)
        return (0 if cost == 0 else 1, -(damage / max(cost, 1)))

    total = 0.0
    budget = energy
    for card in sorted(attacks, key=_efficiency):
        cost = _cost_int(card)
        if cost <= budget:
            total += _effective_damage(card, enemy, shiv_bonus)
            budget -= cost
    return total


# A boss mechanic that kills on a timer rather than through damage. The Act 2
# boss The Insatiable (321 HP) applies Sandpit -- "In 4 turns, you will be
# eaten and die" -- and hands out Frantic Escape: "Get farther away. Increase
# Sandpit by 1. Increase the cost of this card by 1."
#
# The fight is a survival puzzle, not a damage race: 321 HP is bait. A run
# reached floor 33 and was eaten while attacking into it. Frantic Escape is a
# *Status* card, so without this the clog rule would rank it as dead weight
# and discard the one card that saves the run.
_DEATH_CLOCK_RE = re.compile(
    r"in (\d+) turns?, you will .*die", re.IGNORECASE
)


def _death_clock(enemies: list[dict[str, Any]]) -> tuple[str, int] | None:
    """(status name, turns left) for a countdown that kills us outright."""
    for enemy in enemies:
        for status in enemy.get("status", []) or []:
            text = status.get("text") or status.get("description") or ""
            m = _DEATH_CLOCK_RE.search(text)
            if m:
                name = status.get("name") or status.get("id") or ""
                turns = status.get("amount")
                return name, int(turns if turns is not None else m.group(1))
    return None


def _delays_death_clock(card: dict[str, Any], clock_name: str) -> bool:
    """Does this card push the countdown back?"""
    if not clock_name:
        return False
    text = card.get("description", "") or ""
    return bool(re.search(rf"increase {re.escape(clock_name)}", text, re.IGNORECASE))


# "Choose a card." where every option is a penalty. The Act 2 boss Knowledge
# Demon (379 HP, and it heals) forces one of these every few turns:
#
#     Disintegration  "At the end of your turn, take 6 damage."
#     Mind Rot        "Draw 1 fewer card each turn."
#     Sloth           "You cannot play more than 3 cards each turn."
#
# Every one scores 0 block and 0 damage, so the mid-combat ranking tied at 0.0
# and `max()` returned whatever the game listed first. A live run took
# Disintegration twice -- 13 self-damage per turn for the rest of a long
# fight -- and died at floor 33 with 14 HP.
#
# Ordering, per the user who plays this matchup:
#   * **Mind Rot is almost always the first pick.** One fewer card a turn is a
#     consistency tax; it does not compound and it cannot kill.
#   * A hard cap on cards played per turn is worse -- it throttles every turn
#     of a long fight and no amount of Block plays around it.
#   * Recurring HP loss is worst *when we cannot cover it*, because it
#     compounds and bypasses nothing. But a deck that reliably blocks absorbs
#     it, so with real Block behind us the bleed becomes the cheap option
#     again and can beat even Mind Rot.
_PENALTY_HP_RE = re.compile(r"(?:take|lose)\s+(\d+)\s+(?:damage|HP)", re.IGNORECASE)
_PENALTY_DRAW_RE = re.compile(r"draw (\d+) fewer", re.IGNORECASE)
_PENALTY_PLAY_CAP_RE = re.compile(r"cannot play more than (\d+)", re.IGNORECASE)

PENALTY_HP_WEIGHT_EXPOSED = 10.0    # per point, when Block cannot cover it
PENALTY_HP_WEIGHT_COVERED = 2.0     # per point, when the deck blocks it comfortably
PENALTY_HP_WEIGHT_ARMOURED = 0.4    # per point, when Block dwarfs the bleed
PENALTY_DRAW_WEIGHT = 3.0           # per card of permanently reduced draw
# A cap on cards played per turn hurts very differently depending on the
# build. A shiv deck wins by playing five or six near-free cards a turn, so a
# 3-card cap simply switches it off; a poison deck plays two or three
# high-impact cards and hardly notices. Weight it by the build we are on.
PENALTY_PLAY_CAP_WEIGHT = 8.0        # per card below a normal turn's plays
PENALTY_PLAY_CAP_BY_ARCHETYPE = {
    "shiv": 20.0,      # never take this one on a shiv build
    "poison": 4.0,     # tolerable: poison plays few cards anyway
    "discard": 10.0,   # discard engines want volume too
}
NORMAL_PLAYS_PER_TURN = 4
CARDS_DRAWN_PER_TURN = 5
BLOCK_COMFORT_FACTOR = 2.0          # block/turn at this multiple of the bleed = covered
BLOCK_ARMOURED_FACTOR = 4.0         # ...and at this multiple, the bleed is cheap


def _block_per_turn(gs: GameState) -> float:
    """Roughly how much Block this deck puts up in a turn.

    Averages the Block printed across the whole deck and scales by a normal
    draw. Crude, but it separates a deck that blocks every turn from one that
    barely blocks at all, which is the only distinction needed here.
    """
    # `GameState` exposes `full_deck_names()` (names only) but no dict-level
    # whole-deck accessor, so build it from the piles. Getting this wrong is
    # silent: an attribute that does not exist just yields 0.0 and the
    # "covered" branch never fires.
    deck = list(gs.hand) + list(gs.draw_pile) + list(gs.discard_pile) + list(gs.exhaust_pile)
    deck = [c for c in deck if isinstance(c, dict)]
    if not deck:
        return 0.0
    dexterity = _player_dexterity(gs)
    total = sum(_effective_block(c, dexterity) for c in deck)
    return (total / len(deck)) * CARDS_DRAWN_PER_TURN


def _penalty_cost(card: dict[str, Any], gs: GameState | None = None) -> float:
    """How harmful an option is when every choice on offer is bad.

    Higher is worse. Returns 0.0 for anything that does not look like a
    penalty, so a screen with a genuinely good option is unaffected.
    """
    text = card.get("description", "") or card.get("text", "") or ""
    cost = 0.0

    m = _PENALTY_HP_RE.search(text)
    if m:
        bleed = int(m.group(1))
        block = _block_per_turn(gs) if gs is not None else 0.0
        if block >= bleed * BLOCK_ARMOURED_FACTOR:
            weight = PENALTY_HP_WEIGHT_ARMOURED
        elif block >= bleed * BLOCK_COMFORT_FACTOR:
            weight = PENALTY_HP_WEIGHT_COVERED
        else:
            weight = PENALTY_HP_WEIGHT_EXPOSED
        cost += bleed * weight

    m = _PENALTY_DRAW_RE.search(text)
    if m:
        cost += int(m.group(1)) * PENALTY_DRAW_WEIGHT

    m = _PENALTY_PLAY_CAP_RE.search(text)
    if m:
        cap = int(m.group(1))
        weight = PENALTY_PLAY_CAP_WEIGHT
        if gs is not None:
            archetype = card_db.dominant_archetype(gs.full_deck_names(), gs.relics)
            weight = PENALTY_PLAY_CAP_BY_ARCHETYPE.get(archetype, weight)
        cost += max(0, NORMAL_PLAYS_PER_TURN - cap) * weight

    return cost


def _choose_target(card: dict[str, Any], enemies: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pick who to point a card at.

    Default is the lowest-effective-HP enemy (finish things off). The
    exception is Weak: it only reduces *Attack* damage, so applying it to an
    enemy that isn't attacking does literally nothing -- a live run kept
    doing exactly that. For Weak cards, prefer the hardest-hitting attacker
    and only fall back to lowest-HP if nobody is attacking at all.
    """
    if not enemies:
        return None
    if _card_applies_weak(card):
        attackers = [e for e in enemies if _enemy_attack_damage(e) > 0]
        if attackers:
            return max(attackers, key=_enemy_attack_damage)

    # Attacking an enemy turns us to face it. That matters whenever we are
    # Surrounded ("receive 50% more damage if attacked from behind. Use
    # targeting cards or potions to change your orientation"): an enemy
    # flagged Back Attack is behind us, and hitting it removes the 50% bonus
    # from its incoming swing. Reported live at the Act 2 boss --
    #
    #     Crusher  188hp  Attack 9x2   <- behind us, ignored
    #     Rocket   120hp  Buff         <- attacked instead
    #
    # -- where turning to face Crusher was worth more than any damage dealt.
    # Ranked ahead of "easiest to finish" only for enemies actually winding up
    # to hit us; a Back Attack enemy that is not attacking costs us nothing
    # this turn.
    behind = [
        e for e in enemies
        if _enemy_attacks_from_behind(e) and _enemy_attack_damage(e) > 0
    ]
    if behind:
        return max(behind, key=_enemy_attack_damage)
    return _lowest_hp_enemy(enemies)


# Whispering Earring plays your first turn for you. Anything we do on that
# turn is therefore wasted -- the cards are spent by the relic, not by us --
# so the only useful action is a potion, which the potion step above has
# already had its chance at.
#
# Not yet seen in any logged run, so it is matched on the relic name *and* on
# the effect wording; whichever the payload actually carries will hit.
_AUTO_FIRST_TURN_RE = re.compile(
    r"(?:plays?|play) (?:your )?first turn"
    r"|first turn is (?:auto|played)"
    r"|automatically plays",
    re.IGNORECASE,
)


def _battle_round(gs: GameState) -> int:
    """1 on the first turn of a fight. 0 when the payload does not say."""
    battle = (gs.raw or {}).get("battle") or {}
    try:
        return int(battle.get("round") or 0)
    except (TypeError, ValueError):
        return 0


def _first_turn_is_played_for_us(gs: GameState) -> bool:
    for relic in gs.relics:
        name = (relic.get("name") or "").lower()
        if "whispering earring" in name:
            return True
        text = relic.get("description") or relic.get("text") or ""
        if _AUTO_FIRST_TURN_RE.search(text):
            return True
    return False


def _attack_priority(
    card: dict[str, Any],
    target: Optional[dict[str, Any]],
    target_already_vulnerable: bool,
    hand: list[dict[str, Any]],
    shiv_bonus: int,
    turns_left: float,
    gs: GameState,
) -> tuple[bool, int, bool, float]:
    """Ranking for "which attack do we play next", newest consideration first.

    * Paying energy for a Sly card wastes its keyword -- it would have played
      free if discarded -- so any non-Sly option comes first.
    * Play-timing next, so order-dependent cards land in the right part of the
      turn (Flechettes early, Finisher last).
    * Then setting up Vulnerable on a target that does not have it yet, since
      every later hit this turn benefits.
    * Finally raw ranking damage.

    Shared by the free-damage step and the main damage step, which had
    byte-identical copies of this.
    """
    sets_up_vulnerable = _card_applies_vulnerable(card) and not target_already_vulnerable
    return (
        not card_db.is_sly(card),
        _play_timing(card, hand),
        sets_up_vulnerable,
        _ranking_damage(card, target, shiv_bonus, hand, turns_left, gs),
    )


class _TurnContext(NamedTuple):
    """Everything the decision steps share, computed once at the top of a turn.

    These were sixty lines of locals at the head of `decide()`. They are all
    interdependent -- `must_block` needs `unblocked` needs `current_block` --
    and several carry hard-won reasoning in their comments, so they are lifted
    out together rather than recomputed per step. `decide()` unpacks them back
    into locals, which keeps every step body byte-identical.
    """

    shiv_bonus: int
    strength: int
    damage_multiplier: float
    active_enemies: list
    dexterity: int
    turns_left: float
    current_block: int
    threatening: list
    incoming: int
    hp_loss: int
    unblocked: int
    must_block: bool
    # The leader of a Minion-flagged fight, and the same leader again when it
    # is close enough to kill that its minions stop drawing fire.
    leader: Optional[dict] = None
    focus_leader: Optional[dict] = None


def _turn_context(
    gs: GameState, all_enemies: list[dict[str, Any]], hand: list[dict[str, Any]]
) -> "_TurnContext":
    """Compute the shared per-turn state. Pure: reads `gs`, decides nothing."""
    shiv_bonus = _shiv_damage_bonus(gs)
    # Our own Strength and damage multipliers change what actually lands, so
    # the lethal check has to see them or it will claim kills that cannot
    # happen (negative Strength) and pass up kills that can (Double Damage).
    strength = _player_strength(gs)
    damage_multiplier = _player_damage_multiplier(gs)  # target-independent part

    # Enemies Poison alone will finish off need no more of our *cards* -- drop
    # them from targeting so we don't waste damage on something already dead.
    # (Falls back to all_enemies if everyone is doomed, so AOE-target
    # fallbacks still have a target.)
    active_enemies = [e for e in all_enemies if _effective_hp(e) > 0] or all_enemies
    # A spent enemy (see `_is_spent`) is no target while anything else stands.
    targetable = [e for e in active_enemies if not _is_spent(e)]
    if targetable:
        active_enemies = targetable

    # Survival math, computed up front: the buff step below needs it to avoid
    # spending energy on scaling when we can't afford to cover a real hit.
    dexterity = _player_dexterity(gs)
    # How long this fight still has to run. Effects that pay out over time
    # (poison above all) are worth what the remaining turns will collect,
    # so this is needed by several steps -- compute it once, up front,
    # rather than inside whichever branch happened to need it first.
    turns_left = _expected_turns_to_kill(active_enemies, hand, shiv_bonus)
    # Block we already have, plus block our own statuses (Plating etc.) will
    # grant for free -- counting the latter avoids overblocking, i.e. spending
    # cards and energy on damage that was already covered.
    current_block = gs.player.get("block", 0) + _pending_block_from_status(gs)
    # A poison-doomed enemy dies before it acts, so its attack never lands and
    # must NOT be blocked against. Note the fallback in `active_enemies` above
    # is for *targeting* only -- when every enemy is doomed, nothing attacks,
    # and using that fallback here would invent damage that can't happen.
    threatening = [e for e in all_enemies if _effective_hp(e) > 0]

    # Status/curse cards in hand hurt us regardless: Infection's end-of-turn
    # damage lands whether or not a single enemy survives to swing. It is
    # therefore added outside the enemy filter -- the case where every enemy
    # is doomed but an Infection still kills us is exactly the one that gets
    # missed if these are conflated.
    raw_incoming = (
        _incoming_damage(threatening)
        + _hand_curse_damage(gs.hand)
        + _status_self_damage(gs)
        # Tainted already taken this turn lands on every attack hit.
        + _player_tainted(gs) * _attack_hits(threatening)
    )
    incoming = _mitigated_incoming(gs, threatening, raw_incoming)
    # Block cannot answer "lose N HP", so it must not drive how much Block we
    # buy -- otherwise the bot spends its whole turn Defending against damage
    # that lands anyway. It still counts toward `incoming` for the survival
    # question ("will I be alive next turn?"), just not toward the Block gap.
    hp_loss = _hand_hp_loss(gs.hand) + _status_hp_loss(gs)
    blockable = max(0, incoming - hp_loss)
    unblocked = max(0, blockable - current_block)
    safety_floor = max(SAFETY_HP_ABS_FLOOR, SAFETY_HP_PCT_FLOOR * gs.max_hp)
    chip_threshold = max(CHIP_DAMAGE_ABS, CHIP_DAMAGE_PCT * gs.max_hp)
    # Long fights invert the block/attack trade. Total damage taken is roughly
    # (incoming per turn x turns the fight lasts), so in a drawn-out elite the
    # cheapest way to take less damage is to *end it sooner*. Blocking 4 a
    # turn for ten turns costs far more than eating 12 across three while
    # killing faster -- the bot was grinding elites down while being chipped
    # to death, most visibly against the one that ended four runs.
    if _should_race(gs, threatening, hand, shiv_bonus):
        chip_threshold *= RACE_CHIP_MULTIPLIER
    # Survival still counts the unblockable portion: it lands regardless.
    total_taken = unblocked + hp_loss
    is_chip_damage = total_taken <= chip_threshold and (gs.hp - total_taken) >= safety_floor
    if _unblocked_damage_is_lethal(gs):
        is_chip_damage = False  # The Gambit: a single unblocked point is death
    if _fight_ends_this_turn(gs):
        # Nothing left to buy with chip damage -- there is no next turn to
        # race towards -- and the HP lost carries into the rest of the run.
        is_chip_damage = False
    must_block = unblocked > 0 and not is_chip_damage
    leader = _flagged_leader(active_enemies, all_enemies)
    focus_leader = (
        leader
        if leader is not None and _leader_in_reach(leader, hand, gs.energy, shiv_bonus)
        else None
    )
    return _TurnContext(
        shiv_bonus=shiv_bonus,
        strength=strength,
        damage_multiplier=damage_multiplier,
        active_enemies=active_enemies,
        dexterity=dexterity,
        turns_left=turns_left,
        current_block=current_block,
        threatening=threatening,
        incoming=incoming,
        hp_loss=hp_loss,
        unblocked=unblocked,
        must_block=must_block,
        leader=leader,
        focus_leader=focus_leader,
    )


def _step_death_clock(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Escape a countdown that kills us outright (Sandpit). See step 0.8."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    shiv_bonus = ctx.shiv_bonus
    must_block = ctx.must_block

    # 0.8. A death clock outranks everything. Nothing else matters if we are
    # eaten in two turns, and the escape card's cost grows by 1 every time it
    # is played -- so play it while it is still affordable rather than saving
    # energy for damage we will not live to land.
    clock = _death_clock(active_enemies)
    if clock is not None:
        # NOT `turns_left`: that name already holds expected-turns-to-kill
        # from the setup above and is read by `_ranking_damage` in every
        # later step. Binding the Sandpit counter to it left the rest of
        # the turn ranking damage against a countdown whenever this step
        # fell through, which it does unless the escape is actually played.
        clock_name, clock_turns = clock
        escapes = [
            c for c in gs.hand
            if _delays_death_clock(c, clock_name)
            and c.get("can_play") is True
            and _cost_int(c) <= gs.energy
        ]
        if escapes:
            cheapest = min(escapes, key=_cost_int)
            energy_after = gs.energy - _cost_int(cheapest)

            # Play it every turn we can afford it -- the card's cost climbs by
            # 1 each play, so a turn skipped is a turn bought more expensively
            # later. But while the countdown still has slack, Powers and real
            # damage matter more: the escape should displace a *bad* play (a
            # basic Strike), not a Power or a genuine hit.
            others = [
                c for c in hand
                if c.get("index") != cheapest.get("index")
                and _cost_int(c) <= gs.energy
            ]
            has_power = any((c.get("type") or "").lower() == "power" for c in others)
            target = _lowest_hp_enemy(active_enemies)

            def _alternative_value(card: dict[str, Any]) -> float:
                block = _effective_block(card, dexterity)
                damage = (
                    _effective_damage(card, target, shiv_bonus, hand)
                    if target is not None else _card_damage(card)
                )
                return max(float(block), float(damage))

            best_alternative = max(
                (_alternative_value(c) for c in others), default=0.0
            )
            worth_displacing = (
                not has_power and best_alternative <= DEATH_CLOCK_WEAK_PLAY_BAR
            )

            # The Block we need to live through *this* turn still outranks it,
            # unless the clock is about to run out -- at which point blocking
            # only changes what kills us.
            block_cards = [c for c in hand if _effective_block(c, dexterity) > 0]
            cheapest_block = min((_cost_int(c) for c in block_cards), default=0)
            can_still_block = (not must_block) or energy_after >= cheapest_block
            last_chance = clock_turns <= DEATH_CLOCK_LAST_CHANCE

            if (worth_displacing and can_still_block) or last_chance:
                fields = {"card_index": cheapest["index"]}
                if _needs_target(cheapest):
                    fields["target"] = _choose_target(cheapest, active_enemies)["entity_id"]
                return "play_card", fields
    return None


def _step_thorns(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Put Block up before swinging into Thorns. See step 0.9."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    unblocked = ctx.unblocked
    must_block = ctx.must_block
    shiv_bonus = ctx.shiv_bonus
    strength = ctx.strength
    current_block = ctx.current_block

    # 0.9. Thorns: put Block up before swinging. Thorns bills us once per
    # hit, so a five-Shiv turn into 2 Thorns costs 10 HP -- and it is charged
    # the moment we attack, which means an unblocked lethal can kill the enemy
    # and still take the retaliation on bare HP. Block first and the same
    # damage lands on the Block instead. `_thorns_cost` already discounts
    # thorns when *ranking* cards, but nothing made the bot block first.
    #
    # Stops as soon as any Block is up, so this cannot loop: one block card,
    # then straight back to the normal steps.
    if current_block <= 0 and any(_enemy_thorns(e) > 0 for e in active_enemies):
        thorns_block = [
            c for c in hand
            if _effective_block(c, dexterity) > 0 and _cost_int(c) <= gs.energy
        ]
        if thorns_block:
            best_guard = max(thorns_block, key=lambda c: _effective_block(c, dexterity))
            fields = {"card_index": best_guard["index"]}
            if _needs_target(best_guard):
                fields["target"] = _choose_target(best_guard, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_stun_threshold(
    gs: GameState,
    hand: list[dict[str, Any]],
    ctx: "_TurnContext",
    attack_options: list[dict[str, Any]],
) -> Optional[tuple[str, dict[str, Any]]]:
    """Cross an HP threshold that stuns a whole enemy turn. See step 1.15."""
    active_enemies = ctx.active_enemies
    shiv_bonus = ctx.shiv_bonus
    strength = ctx.strength

    # 1.15. Trip a stun threshold. Crossing one cancels a whole turn of that
    # enemy's damage -- worth as much as the block it saves -- and without
    # this the bot treats 71 HP and 69 HP as identical. Only taken when the
    # enemy is actually about to swing, so the cancelled turn is a real one.
    for enemy in sorted(active_enemies, key=_enemy_attack_damage, reverse=True):
        if _enemy_attack_damage(enemy) <= 0:
            continue
        needed = _damage_to_stun(enemy)
        if needed is None or _reachable_damage(hand, enemy, gs.energy, shiv_bonus) < needed:
            continue
        stunner = max(
            (c for c in attack_options),
            key=lambda c: (not card_db.is_sly(c), _effective_damage(c, enemy, shiv_bonus)),
            default=None,
        )
        if stunner is not None:
            fields = {"card_index": stunner["index"]}
            if _needs_target(stunner):
                fields["target"] = enemy["entity_id"]
            return "play_card", fields
    return None


# Paying for a Sly card spends energy on something a discard would play for
# free. When the other Block cards leave no more than a Defend's worth
# unblocked, and we can afford that, take the chip and keep the Sly card.
# Live: 12 unblocked at 30 HP holding Untouchable ("Sly. Gain 9 Block"),
# Defend and two Strikes -- the bot paid 2 energy for Untouchable, where
# Defend + Strike + Strike takes 4 and deals 12.
SLY_KEEP_MAX_CHIP = 5


def _keep_sly_for_discard(
    gs: GameState, block_cards: list[dict[str, Any]], dexterity: int, unblocked: int
) -> list[dict[str, Any]]:
    """The Block cards worth paying for, holding Sly ones back when we can."""
    if (
        _unblocked_damage_is_lethal(gs)
        or _fight_ends_this_turn(gs)  # no later turn to discard it on
        or not any(card_db.is_sly(c) for c in block_cards)
    ):
        return block_cards
    others = [c for c in block_cards if not card_db.is_sly(c)]
    plan = _best_block_plan(others, gs.energy, dexterity, unblocked, _plays_left(gs))
    residual = max(0, unblocked - sum(_effective_block(c, dexterity) for c in plan))
    safety_floor = max(SAFETY_HP_ABS_FLOOR, SAFETY_HP_PCT_FLOOR * gs.max_hp)
    if residual <= SLY_KEEP_MAX_CHIP and gs.hp - residual >= safety_floor:
        return others
    return block_cards


# Fan of Knives ("Shivs now hit ALL enemies. Add 4 Shivs into your Hand.")
# multiplies every Shiv by the enemy count, so against two or more it is the
# best play in the hand -- the user's call, and the logs agree: at floor 9
# against three enemies the bot spent 2 energy on Pounce to kill a 9 HP slime
# that four all-enemy Shivs would have killed anyway, alongside 16 into each
# of the other two. It goes ahead of the lethal step for exactly that reason.
def _step_fan_of_knives(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Make Shivs hit everything before spending them. See step 0.95."""
    if _shivs_hit_all(gs) or ctx.unblocked >= gs.hp:
        return None
    if len([e for e in ctx.active_enemies if _effective_hp(e) > 0]) < 2:
        return None
    for card in hand:
        if _SHIVS_HIT_ALL_RE.search(card.get("description") or "") and _cost_int(card) <= gs.energy:
            fields: dict[str, Any] = {"card_index": card["index"]}
            if _needs_target(card):
                fields["target"] = _choose_target(card, ctx.active_enemies)["entity_id"]
            return "play_card", fields
    return None


# Echoing Slash: "Deal 10 damage to ALL enemies. Repeat this effect for each
# enemy killed." A kill buys another full wave, so one kill can clear the
# board. Read as a single 10-to-all it lost every such race: live, Echoing
# Slash+ (18) sat in hand against three Tough Eggs on 18/17/16 and an
# Ovicopter on 73 -- the chain kills all three eggs and puts 72 into
# Ovicopter, leaving it on 1 -- while Murder+ went into Ovicopter instead.
_CHAIN_RE = re.compile(r"repeat this effect for each enemy killed", re.IGNORECASE)


def _chain_kills(
    card: dict[str, Any],
    enemies: list[dict[str, Any]],
    shiv_bonus: int,
    hand: Optional[list[dict[str, Any]]] = None,
) -> tuple[int, bool]:
    """(kills, board cleared) for an all-enemy card that repeats on each kill."""
    live = [e for e in enemies if (e.get("hp") or 0) > 0]
    if not live or not _CHAIN_RE.search(card.get("description") or ""):
        return 0, False
    remaining = {id(e): _damage_to_kill(e) for e in live}
    wave = {id(e): _effective_damage(card, e, shiv_bonus, hand) for e in live}
    waves, kills, alive = 1, 0, list(live)
    while waves and alive:
        waves -= 1
        died = []
        for enemy in alive:
            remaining[id(enemy)] -= wave[id(enemy)]
            if remaining[id(enemy)] <= 0:
                died.append(enemy)
        kills += len(died)
        waves += len(died)
        alive = [e for e in alive if e not in died]
    return kills, not alive


def _step_chain_kill(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """An all-enemy card that repeats on every kill goes first when it kills
    now -- the user's call. See `_chain_kills`."""
    live = [e for e in gs.enemies if (e.get("hp") or 0) > 0]
    if len(live) < 2:
        return None
    best: Optional[tuple[tuple[bool, int], dict[str, Any]]] = None
    for card in hand:
        if _cost_int(card) > gs.energy:
            continue
        kills, clears = _chain_kills(card, live, ctx.shiv_bonus, hand)
        if kills and (best is None or (clears, kills) > best[0]):
            best = ((clears, kills), card)
    if best is None:
        return None
    (clears, _kills), card = best
    if ctx.unblocked >= gs.hp and not clears:
        return None  # surviving the hit comes first, unless this ends the fight
    fields: dict[str, Any] = {"card_index": card["index"]}
    if _needs_target(card):
        fields["target"] = _choose_target(card, ctx.active_enemies)["entity_id"]
    return "play_card", fields


# Normality is a Curse *in hand* -- "You cannot play more than 3 cards this
# turn. (3 cards left)" -- not a status, so `_play_limit` never saw it. A
# capped turn spends plays as well as energy: live, with 24 incoming and three
# plays, the bot spent all three on attacks and ended on 0 Block.
_CARDS_LEFT_RE = re.compile(
    r"cannot play more than \d+ cards?[^(]*\((\d+) cards? left\)", re.IGNORECASE
)


def _plays_left(gs: GameState) -> int | None:
    """Card plays still allowed this turn, when anything caps them."""
    caps = []
    limit = _play_limit(gs)
    if limit is not None:
        caps.append(limit)
    for card in gs.hand:
        m = _CARDS_LEFT_RE.search(card.get("description") or "")
        if m:
            caps.append(int(m.group(1)))
    return min(caps) if caps else None


# Smoggy: "You can only play 1 Skill per turn" (Living Fog). The bot reads
# `can_play`, so it never tries an illegal card, but the block plan counted
# every Skill in hand -- two Defends beat one bigger card on paper when only
# one of them could be played. Set 3, run 10, floor 7.
_SKILL_LIMIT_RE = re.compile(r"can only play (\d+) Skills? (?:per|each|a) turn", re.IGNORECASE)


def _skill_limit(gs: GameState) -> Optional[int]:
    """How many Skills we may play this turn, or None if unlimited."""
    for status in gs.player.get("status") or []:
        m = _SKILL_LIMIT_RE.search(status.get("description") or "")
        if m:
            return int(m.group(1))
    return None


def _is_skill(card: dict[str, Any]) -> bool:
    return (card.get("type") or "").lower() == "skill"


_DRAWS_RE = re.compile(r"\bdraw (\d+) cards?", re.IGNORECASE)
DRAW_FIRST_HAND_CAP = 10  # a draw into a full hand is thrown away


def _draw_before_block(
    hand: list[dict[str, Any]], plan: list[dict[str, Any]], energy: int, covered: int, need: int
) -> Optional[dict[str, Any]]:
    """A draw card worth playing before the Block, or None.

    When the best Block in hand still falls short and the plan leaves energy
    over, drawing first can only add options -- the plan stays affordable.
    Set 3, run 3, the Waterfall Giant's death blow: 45 into 30 HP, both
    Defends (14) went first and Acrobatics then drew with no energy left to
    play what it found; Survivor + a Defend would have lived.
    """
    if covered >= need or len(hand) >= DRAW_FIRST_HAND_CAP:
        return None
    spare = energy - sum(_cost_int(c) for c in plan)
    drawers = [
        c for c in hand
        if c not in plan
        and _DRAWS_RE.search(c.get("description") or "")
        and (c.get("type") or "").lower() not in ("status", "curse")
        and not card_db.is_sly(c)  # a Sly draw card is discarded, not paid for
        and _cost_int(c) <= spare
    ]
    if not drawers:
        return None
    return max(drawers, key=lambda c: (int(_DRAWS_RE.search(c["description"]).group(1)), -_cost_int(c)))


def _best_block_plan(
    block_cards: list[dict[str, Any]],
    energy: int,
    dexterity: int,
    need: int,
    max_cards: int | None = None,
    taint_hits: int = 0,
    max_skills: int | None = None,
) -> list[dict[str, Any]]:
    """The affordable set of Block cards that covers the most of `need`.

    Ties go to the cheaper set, then to fewer cards. A hand holds at most ten
    cards, so every subset is simply tried.
    """
    best: list[dict[str, Any]] = []
    best_key = (0, 0, 0)
    n = len(block_cards)
    for mask in range(1, 1 << n):
        chosen = [block_cards[i] for i in range(n) if mask >> i & 1]
        if max_cards is not None and len(chosen) > max_cards:
            continue
        if max_skills is not None and sum(1 for c in chosen if _is_skill(c)) > max_skills:
            continue
        cost = sum(_cost_int(c) for c in chosen)
        if cost > energy:
            continue
        # A Tainted Skill's Block is net of the extra damage it adds.
        covered = min(need, sum(
            max(0, _effective_block(c, dexterity) - _tainted_gain(c) * taint_hits) for c in chosen
        ))
        key = (covered, -cost, -len(chosen))
        if key > best_key:
            best, best_key = chosen, key
    return best


def _step_survive(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Block when the incoming hit is worth blocking. See step 2."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    must_block = ctx.must_block
    shiv_bonus = ctx.shiv_bonus
    turns_left = ctx.turns_left
    unblocked = ctx.unblocked

    # 2. Survive: need-based, not automatic. Doomed enemies won't get a turn
    # to act, so their queued attacks don't count either -- and if nothing is
    # attacking at all, there's nothing to block and we skip this entirely.
    # Otherwise block unless the hit is mere chip damage we're healthy enough
    # to absorb for tempo (take 1 to deal 6). When it IS worth blocking, use
    # the smallest card that closes the gap rather than overshooting.
    # (incoming/unblocked/must_block computed above -- the buff step needs them.)
    if must_block:
        block_cards = _keep_sly_for_discard(
            gs, [c for c in hand if _effective_block(c, dexterity) > 0], dexterity, unblocked
        )
        # (Flechettes-before-block is handled by the sequencing step above.)
        if block_cards:
            taint_hits = _attack_hits(ctx.threatening)

            def _blk(c: dict[str, Any]) -> int:
                # Net of any Tainted the card adds -- see `_tainted_gain`.
                return max(0, _effective_block(c, dexterity) - _tainted_gain(c) * taint_hits)

            sufficient = [c for c in block_cards if _blk(c) >= unblocked]
            if sufficient:
                pool = sufficient
            else:
                # No single card covers it, so the question is which *set*
                # covers the most for the energy we have. Taking the biggest
                # single card ignored its cost: Untouchable (2 energy, 6
                # Block) beat Defend's 5, spending both energy on 6 Block when
                # two Defends make 10. Live: 15 incoming, three Defends in
                # hand, ended on 6 and took 9. Within the plan, the biggest
                # card still goes first.
                limit = _skill_limit(gs)
                plan = _best_block_plan(
                    block_cards, gs.energy, dexterity, unblocked, _plays_left(gs), taint_hits, limit,
                )
                # Short on Block when the shortfall would kill us, with energy
                # to spare: draw first -- see `_draw_before_block`. Not under a
                # play cap (Normality) or a Skill cap (Smoggy): the draw would
                # spend the play the Block needs. Firing whenever Block fell
                # short changed 492 archived decisions, mostly a free Prepared
                # before a Defend on turns that were never in danger.
                if _plays_left(gs) is None and limit is None and unblocked + ctx.hp_loss >= gs.hp:
                    drawer = _draw_before_block(
                        hand, plan, gs.energy, sum(_blk(c) for c in plan), unblocked
                    )
                    if drawer is not None and not (_tainted_gain(drawer) and taint_hits):
                        fields = {"card_index": drawer["index"]}
                        if _needs_target(drawer):
                            fields["target"] = _choose_target(drawer, active_enemies)["entity_id"]
                        return "play_card", fields
                pool = plan or block_cards

            # A card that blocks *and* attacks (Dash: "Gain 10 Block. Deal 10
            # damage.") strictly beats a plain Defend when either would cover
            # the hit -- the damage is free. Picking the smallest sufficient
            # block is right for conserving cards, but it was throwing away
            # Dash's damage in favour of a Defend.
            two_for_one = [c for c in pool if _card_damage(c) > 0 or _card_poison(c) > 0]
            if two_for_one:
                target = _lowest_hp_enemy(active_enemies)
                best = max(
                    two_for_one,
                    key=lambda c: _ranking_damage(c, target, shiv_bonus, hand, turns_left, gs)
                    if target
                    else _card_damage(c),
                )
            else:
                # Among otherwise-comparable block cards, prefer one whose
                # discard triggers a Sly card in hand -- Survivor next to a Sly
                # attack is block *plus* a free attack, where a plain Defend is
                # just block.
                sly_enablers = [c for c in pool if _sly_payoff_available(c, hand)]
                if sly_enablers:
                    best = min(sly_enablers, key=_blk) if sufficient else max(sly_enablers, key=_blk)
                else:
                    best = min(pool, key=_blk) if sufficient else max(pool, key=_blk)
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
    return None


# Sly cards meant to be discarded, not paid for: Reflex ("Sly. Draw 2 cards.",
# 3 energy) and Tactician ("Sly. Gain 1 Energy.", 3 energy). Paid for,
# Tactician loses 2 energy and Reflex draws 2 cards with nothing left to play
# them. Set 3, run 3, the Giant on a Heal turn: 3 energy on Reflex with Pounce
# + Strike (20) in hand. Across 108 logs Reflex was paid for 19 times, 18 of
# them with an attack affordable. The leftover-energy step may still play one
# when nothing else can use the energy.
_SLY_DRAW_OR_ENERGY_RE = re.compile(
    r"^\s*Sly\.\s*(?:Draw \d+ cards?|Gain (?:\d+ Energy|(?:\[[^\]]*\])+))\.?\s*$", re.IGNORECASE
)


def _sly_pays_for_nothing(card: dict[str, Any]) -> bool:
    return (
        card_db.is_sly(card)
        and _cost_int(card) >= 2
        and bool(_SLY_DRAW_OR_ENERGY_RE.search(card.get("description") or ""))
    )


def _step_value(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Best-synergy non-attack play for the deck. See step 3."""
    active_enemies = ctx.active_enemies
    must_block = ctx.must_block

    # 3. Value: best-synergy non-attack play for the deck (debuffs, draw, sly, etc).
    # Pure block cards are excluded here when blocking isn't actually needed --
    # otherwise a Defend can still win on generic archetype score alone (decks
    # are full of them) even when step 2 just determined it's safe to skip.
    deck_names = gs.full_deck_names()
    counts = card_db.deck_tag_counts(deck_names)
    # Status and Curse cards are never a *value* play, whatever they score.
    # Slimed ("Draw 1 card. Exhaust.") is playable and unknown to the card
    # data, so once unknown cards began scoring a middling 30 instead of 0 it
    # cleared this step's >=20 gate and the bot spent whole turns exhausting
    # Slimed instead of attacking. Unknown-is-not-bad is right for a card
    # *reward*; a Status card in hand is known-bad by type. They can still be
    # played by the leftover-energy step when there is nothing better to do.
    value_candidates = [
        c for c in hand
        if not _is_attack_option(c)
        and (must_block or not _is_pure_block(c))
        and (c.get("type") or "").lower() not in ("status", "curse")
        and not _sly_pays_for_nothing(c)
    ]
    # A hand clogged with Status/Curse cards has no answer except an outlet,
    # so once the clog is real those cards outrank their printed score --
    # which is usually low, because discarding one card reads as weak until
    # the card being discarded is an Infection dealing 3 a turn.
    clog = _dead_cards_in_hand(gs.hand, active_enemies)
    clogged = clog >= CLOG_THRESHOLD
    if value_candidates:
        scored = sorted(
            value_candidates,
            key=lambda c: (
                clogged and _clears_clog(c),
                not card_db.is_sly(c),
                # A discard card that triggers a Sly card is worth far more
                # than its own text suggests -- it's a free extra play.
                _sly_payoff_available(c, hand),
                card_db.score_card(c["name"], counts),
            ),
            reverse=True,
        )
        best = scored[0]
        # An outlet bypasses the score gate while the hand is clogged: its own
        # score is not the point, clearing the dead weight is.
        if (clogged and _clears_clog(best)) or card_db.score_card(best["name"], counts) >= 20:
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                # Weak-applying *skills* (Leg Sweep, Malaise) route through
                # this value step, not the attack step -- targeting lowest-HP
                # here is how Weak kept landing on non-attacking enemies.
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_damage(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Spend remaining energy on the weakest reachable enemy. See step 4."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    shiv_bonus = ctx.shiv_bonus
    turns_left = ctx.turns_left
    unblocked = ctx.unblocked

    # 4. Damage: spend remaining energy on the weakest enemy. Attacks that
    # apply Vulnerable to a not-yet-debuffed target are sequenced first so
    # whatever we play right after (this turn, and future polls this turn)
    # benefits from the +50%; ties broken by total effective damage.
    #
    # Energy reservation: if there's unblocked damage a block card would
    # efficiently absorb, hold back that card's cost so attacks can't eat the
    # last energy. Without this, step 5 (leftover block) never actually got
    # any energy -- attacks always consumed it first -- and small hits piled
    # up turn after turn into serious chip damage.
    attack_candidates = [c for c in hand if _is_attack_option(c)]
    if attack_candidates:
        reserve = 0
        if unblocked > 0:
            efficient_block = [
                c
                for c in hand
                if _effective_block(c, dexterity) > 0
                and unblocked >= _effective_block(c, dexterity) * BLOCK_EFFICIENCY_RATIO
            ]
            if efficient_block:
                reserve = min(_cost_int(c) for c in efficient_block)

        affordable = [c for c in attack_candidates if _cost_int(c) <= gs.energy - reserve]
        # Attacking an enemy whose Block we cannot break this turn accomplishes
        # nothing at all -- Block resets, so damage that fails to get through
        # is simply gone. Judged on the turn's *total* reachable damage, not
        # one card, since two Strikes together may break what neither does
        # alone. Poison bypasses Block, so a card carrying it still counts.
        def _can_hurt(enemy: dict[str, Any]) -> bool:
            budget = max(0, gs.energy - reserve)
            if any(_card_poison(c) > 0 for c in affordable):
                return True
            return _reachable_damage(affordable, enemy, budget, shiv_bonus) > _enemy_block(enemy)

        # A Gas Bomb explodes on its own turn whatever we do, so damage that
        # does not finish it is thrown away. Set 3, run 10: Strike (6) into a
        # 7-HP Gas Bomb, which blew up anyway, while Living Fog took nothing.
        budget = max(0, gs.energy - reserve)
        doomed_anyway = [
            e for e in active_enemies
            if _self_destructs(e)
            and _reachable_damage(affordable, e, budget, shiv_bonus) < _damage_to_kill(e)
        ]
        hurtable = [e for e in active_enemies if _can_hurt(e) and e not in doomed_anyway]
        if affordable and not hurtable:
            affordable = []  # nothing we can reach through -- spend the turn elsewhere
        if affordable:
            # Damage that does not kill goes to the leader of a Minion fight --
            # but only when chipping a minion would buy nothing: the leader is
            # within reach, it is summoning more, or none of its minions is
            # attacking (Ovicopter's eggs). Chip on an *attacking* minion is
            # worth it -- it becomes a kill that removes damage. Aiming at a
            # far-off leader regardless moved six attacks off Kin Priest's
            # Followers, who were doing the hitting, onto a 190 HP Priest
            # nowhere near dying; nothing died in eight rounds and the run
            # ended there. Minion *kills* stay the lethal step's call.
            minions_attacking = any(
                _is_minion(e) and _enemy_attack_damage(e) > 0 for e in active_enemies
            )
            aim = None
            if (
                ctx.leader is not None
                and ctx.leader in hurtable
                and (
                    ctx.focus_leader is not None
                    or _resummons(ctx.leader)
                    or not minions_attacking
                )
            ):
                aim = ctx.leader
            # Decimillipede segments revive unless they all fall together, so
            # damage that does not kill evens them out -- see `_reattach_hp`.
            segments = [e for e in hurtable if _reattach_hp(e)]
            if aim is None and len(segments) >= 2:
                aim = max(segments, key=_damage_to_kill)
            target = aim or _lowest_hp_enemy(hurtable)
            target_already_vulnerable = _enemy_has_vulnerable(target)

            best = max(
                affordable,
                key=lambda c: _attack_priority(
                    c, target, target_already_vulnerable, hand, shiv_bonus, turns_left, gs
                ),
            )
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                if aim is not None and not _card_applies_weak(best):
                    fields["target"] = aim["entity_id"]
                else:
                    targets = [e for e in active_enemies if e not in doomed_anyway] or active_enemies
                    fields["target"] = _choose_target(best, targets)["entity_id"]
            return "play_card", fields
    return None


def _self_destructs(enemy: dict[str, Any]) -> bool:
    """An enemy that removes itself on its own turn: a DeathBlow on real HP
    (Gas Bomb). The Waterfall Giant's husk is `_is_spent` instead."""
    return not _is_spent(enemy) and any(
        (i.get("type") or "") == "DeathBlow" for i in enemy.get("intents") or []
    )


def _step_leftover_block(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Turn energy we cannot otherwise use into Block. See step 5."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    unblocked = ctx.unblocked

    # 5. Leftover energy: we're about to end the turn with energy to spare, so
    # any block against real incoming damage is free value now -- unspent
    # energy is simply lost. Only fires when something is actually attacking;
    # blocking into no attack does nothing (block decays at turn start).
    # A live regression ended turns at 8 HP holding two playable Defends
    # because step 2 had waived the block and nothing downstream picked it up.
    if unblocked > 0:
        leftover_block = [c for c in hand if _effective_block(c, dexterity) > 0]
        if leftover_block:
            best = max(leftover_block, key=lambda c: _effective_block(c, dexterity))
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_duplication(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Spend a one-shot damage doubler on the best card. See step 1.02."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    must_block = ctx.must_block
    shiv_bonus = ctx.shiv_bonus
    strength = ctx.strength

    # 1.02. Duplication is a one-shot: whatever we play next resolves twice,
    # so it must be the best card in hand, not merely the next one the
    # pipeline would have reached. The lethal check above already ran with the
    # doubled multiplier, so if a kill were available it has been taken --
    # what is left is to maximise value. Block wins when we actually need it,
    # otherwise damage.
    if _duplicator_active(gs) and hand:
        target = _lowest_hp_enemy(active_enemies)

        def _doubled_value(card: dict[str, Any]) -> float:
            block = _effective_block(card, dexterity) * 2
            damage = (
                _effective_damage(card, target, shiv_bonus, hand, strength, 2.0)
                if target is not None else 0.0
            )
            return float(block) if must_block and block >= damage else float(damage)

        best = max(hand, key=_doubled_value)
        if _doubled_value(best) > 0:
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_capped_turn(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """A turn capped to one card (Ringing) is a single decision. See step 1.05."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    shiv_bonus = ctx.shiv_bonus
    turns_left = ctx.turns_left

    # 1.05. A capped turn (Ringing: "You can only play 1 card this turn") is a
    # single decision, not the first step of a sequence, so every ordering
    # rule below is meaningless -- whatever we choose *is* the turn. The
    # single-card lethal check above already ran, so nothing here can win the
    # fight outright; the best remaining use of one card is the biggest
    # defensive body. Shivs are the trap: 0-cost attacks the bot would
    # normally dump for free, each burning the entire turn for a few damage.
    play_cap = _play_limit(gs)
    if play_cap == 1:
        defensive = [c for c in hand if _effective_block(c, dexterity) > 0]
        if defensive:
            best = max(defensive, key=lambda c: _effective_block(c, dexterity))
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
        # Nothing defensive: spend the one play on the most damage available,
        # still refusing to waste it on a Shiv when anything else exists.
        non_shiv = [c for c in hand if card_db.base_name(c.get("name", "")) != "Shiv"] or hand
        if non_shiv:
            target = _lowest_hp_enemy(active_enemies)
            best = max(non_shiv, key=lambda c: _ranking_damage(c, target, shiv_bonus, hand, turns_left, gs))
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_powers(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Powers and scaling buffs go before anything they scale. See step 1.2."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    must_block = ctx.must_block
    unblocked = ctx.unblocked

    # 1.2. Powers and scaling buffs go before anything they scale. A Power
    # pays off per card played and per turn that follows it (Afterimage,
    # Accuracy, Envenom, Serpent Form...), and Strength/Dexterity pump the
    # very Strikes and Defends we're about to play -- so either one sequenced
    # late is wasted value. Skipped only when we're under real pressure and
    # can't afford both it and enough block to cover the hit: surviving beats
    # scaling.
    buff_cards = [c for c in hand if _is_scaling_buff(c)]
    if buff_cards:
        cheapest_buff = min(buff_cards, key=_cost_int)
        energy_left = gs.energy - _cost_int(cheapest_buff)
        block_cards = [c for c in hand if _effective_block(c, dexterity) > 0]
        cheapest_block = min((_cost_int(c) for c in block_cards), default=None)
        # Deliberately NOT "can one card fully cover the hit?" -- when no
        # single Defend covers it we're taking damage either way, and the
        # Dexterity makes every Defend that follows better. What matters is
        # (a) not dying outright and (b) still affording some block after.
        would_die = unblocked >= gs.hp
        can_still_block = cheapest_block is not None and energy_left >= cheapest_block
        if not must_block or (not would_die and can_still_block):
            fields = {"card_index": cheapest_buff["index"]}
            if _needs_target(cheapest_buff):
                fields["target"] = _choose_target(cheapest_buff, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_sequencing(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Cards worth less if deferred go now. See step 1.4."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    must_block = ctx.must_block
    unblocked = ctx.unblocked

    # 1.4. Sequencing: anything that is worth less if we wait goes now. This
    # replaces three separate guards that each solved one instance of the same
    # problem in a different step. The guard is always the same -- reorder
    # only when the block we still need stays affordable and the incoming hit
    # will not kill us in the meantime -- so survival always wins the tie.
    clog_now = _dead_cards_in_hand(gs.hand, active_enemies)
    is_clogged = clog_now >= CLOG_THRESHOLD
    deferring_costs = [
        c for c in hand
        if _loses_value_if_deferred(c, hand, is_clogged) and _cost_int(c) <= gs.energy
    ]
    if deferring_costs:
        block_cards_now = [c for c in hand if _effective_block(c, dexterity) > 0]
        cheapest_block = min((_cost_int(c) for c in block_cards_now), default=0)

        def _urgency(c: dict[str, Any]) -> tuple[int, float]:
            # Prefer the one that actually loses the most: contextual damage
            # for Flechettes, else fall back on play-timing order.
            return (-_play_timing(c, hand), float(_contextual_damage(c, hand) or 0))

        best_first = max(deferring_costs, key=_urgency)
        energy_after = gs.energy - _cost_int(best_first)
        can_still_block = (not must_block) or energy_after >= cheapest_block
        if can_still_block and unblocked < gs.hp:
            fields = {"card_index": best_first["index"]}
            if _needs_target(best_first):
                fields["target"] = _choose_target(best_first, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_purge_held_status(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Play away a Status card that damages us while held. See step 1.6."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    must_block = ctx.must_block

    # 1.6. Purge cards that hurt us just for being held. Beckon ("if this is
    # in your Hand, lose 6 HP") is a Status the Act 1 boss deals out 1-2 of
    # per turn, and it is `can_play: True` at 1 energy -- so playing it simply
    # deletes the damage. That beats a Defend outright when the loss is
    # unblockable, which Beckon's is. Reported live as "the bot is not playing
    # the 1 energy to discard it, which is better than playing a defend".
    purgeable = [
        c for c in hand
        if _self_damage_in_hand(c) > 0 and _cost_int(c) <= gs.energy
    ]
    if purgeable:
        best_purge = max(purgeable, key=_self_damage_in_hand)
        saved = _self_damage_in_hand(best_purge)
        # Only worth an energy if it beats what that energy would block, and
        # never at the cost of a block we still need to survive the turn.
        block_cards = [c for c in hand if _effective_block(c, dexterity) > 0]
        best_block = max((_effective_block(c, dexterity) for c in block_cards), default=0)
        cheapest_block = min((_cost_int(c) for c in block_cards), default=0)
        energy_after = gs.energy - _cost_int(best_purge)
        still_safe = not must_block or energy_after >= cheapest_block
        if saved >= best_block and still_safe:
            fields = {"card_index": best_purge["index"]}
            if _needs_target(best_purge):
                fields["target"] = _choose_target(best_purge, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_mitigation(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Shrink the incoming hit before sizing Block against it. See step 1.7."""
    active_enemies = ctx.active_enemies
    threatening = ctx.threatening
    unblocked = ctx.unblocked

    # 1.7. Mitigation before block. Weak and enemy-Strength reduction shrink
    # the incoming hit, so playing them *after* block means the block was
    # sized against the bigger number and part of it is wasted. Only worth it
    # when there's real damage coming and someone is actually attacking.
    if unblocked > 0 and any(_enemy_attack_damage(e) > 0 for e in active_enemies):
        # Rank by damage actually prevented, not by the card's own Block.
        # Strength reduction applies *per hit*, so Piercing Wail ("ALL enemies
        # lose 6 Strength this turn") is worth 6 against a single attack and
        # 18 against a three-hit one -- and can cover a whole turn outright.
        # This was previously a yes/no flag, so the amount never entered the
        # decision at all.
        scored = [
            (c, _mitigation_value(c, threatening))
            for c in hand
            if _reduces_incoming_damage(c)
        ]
        scored = [(c, v) for c, v in scored if v >= MIN_MITIGATION_VALUE]
        if scored:
            best = max(scored, key=lambda cv: (cv[1], not card_db.is_sly(cv[0])))[0]
            fields = {"card_index": best["index"]}
            if _needs_target(best):
                fields["target"] = _choose_target(best, active_enemies)["entity_id"]
            return "play_card", fields
    return None


def _step_free_damage(
    gs: GameState,
    hand: list[dict[str, Any]],
    ctx: "_TurnContext",
    attack_options: list[dict[str, Any]],
) -> Optional[tuple[str, dict[str, Any]]]:
    """Dump 0-cost attacks: they never compete for energy. See step 1.5."""
    active_enemies = ctx.active_enemies
    shiv_bonus = ctx.shiv_bonus
    turns_left = ctx.turns_left

    # 1.5. Free damage: 0-cost attack options (Shivs, Neutralize, Backstab...)
    # never compete with anything else for energy, so there's no reason to
    # ever hold one back -- dump the best one now rather than risk it sitting
    # unplayed when the turn ends. Same Vulnerable-first sequencing as step 4.
    free_damage = [c for c in attack_options if _cost_int(c) == 0]
    # (Choke-before-free-damage is handled by the sequencing step above: any
    # collector still affordable has already been played by the time we get
    # here, so this step no longer needs its own carve-out.)
    if free_damage:
        target = _lowest_hp_enemy(active_enemies)
        target_already_vulnerable = _enemy_has_vulnerable(target)

        best = max(
            free_damage,
            key=lambda c: _attack_priority(
                c, target, target_already_vulnerable, hand, shiv_bonus, turns_left, gs
            ),
        )
        fields = {"card_index": best["index"]}
        if _needs_target(best):
            fields["target"] = _choose_target(best, active_enemies)["entity_id"]
        return "play_card", fields
    return None


def _step_final_leftover(
    gs: GameState, hand: list[dict[str, Any]], ctx: "_TurnContext"
) -> Optional[tuple[str, dict[str, Any]]]:
    """Never end a turn holding energy and a playable card. See step 6."""
    active_enemies = ctx.active_enemies
    dexterity = ctx.dexterity
    shiv_bonus = ctx.shiv_bonus
    unblocked = ctx.unblocked

    # 6. Final leftover-energy scan: never end a turn holding energy and a
    # playable card. Energy doesn't carry over, so anything playable is
    # strictly better than wasting it -- and the earlier steps all have bars a
    # card can fail (the value step needs a minimum synergy score, the block
    # steps need incoming damage), which left real cards stranded in hand.
    # `hand` is already filtered to affordable + playable, so anything here is
    # a legal play; pick the best of them by deck synergy.
    # No `energy > 0` gate: a card whose cost dropped to 0 mid-turn is still
    # playable at 0 energy, and gating on energy left those stranded.
    if hand:
        counts = card_db.deck_tag_counts(gs.full_deck_names())

        def _leftover_rank(c: dict[str, Any]) -> tuple[int, int, float]:
            # Prefer cards that do something regardless of the board state
            # over block that would decay unused when nothing is attacking,
            # and keep Sly cards in hand where a later discard plays them free.
            useful_now = 0 if (_effective_block(c, dexterity) > 0 and unblocked <= 0) else 1
            # An attack that cannot break the target's Block is worth nothing
            # this turn -- Block resets, so absorbed damage is simply gone.
            # Reported live: a 6-damage Strike thrown into 7 Block while the
            # enemy was winding up a 13-damage hit. Poison ignores Block, so a
            # card carrying it still counts as useful.
            if _is_attack_option(c) and _card_poison(c) <= 0 and active_enemies:
                if all(
                    _effective_damage(c, e, shiv_bonus, hand) <= _enemy_block(e)
                    for e in active_enemies
                ):
                    useful_now = 0
            return (0 if card_db.is_sly(c) else 1, useful_now, card_db.score_card(c["name"], counts))

        best = max(hand, key=_leftover_rank)
        fields = {"card_index": best["index"]}
        if _needs_target(best):
            fields["target"] = _choose_target(best, active_enemies)["entity_id"]
        return "play_card", fields
    return None


def decide(gs: GameState) -> tuple[str, dict[str, Any]]:
    """Choose this combat action, and remember which enemies it hits --
    Skittish Block depends on hits the payload does not show."""
    action = _decide(gs)
    _note_hits(gs, action)
    return action


def _decide(gs: GameState) -> tuple[str, dict[str, Any]]:
    all_enemies = gs.enemies
    if not all_enemies:
        return "end_turn", {}

    # 0. Potions: clear-cut heal/finish/emergency-block cases only.
    potion_action = potion_db.suggest_potion_use(gs)
    if potion_action:
        return potion_action

    # 0.5. Whispering Earring plays the first turn for us, so playing cards
    # into it just spends them for nothing. Potions are the exception and were
    # offered their chance immediately above.
    if _battle_round(gs) == 1 and _first_turn_is_played_for_us(gs):
        return "end_turn", {}

    hand = _playable_hand(gs)
    if not hand:
        return "end_turn", {}

    ctx = _turn_context(gs, all_enemies, hand)
    shiv_bonus = ctx.shiv_bonus
    strength = ctx.strength
    damage_multiplier = ctx.damage_multiplier
    active_enemies = ctx.active_enemies
    current_block = ctx.current_block
    incoming = ctx.incoming

    action = _step_death_clock(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_thorns(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_fan_of_knives(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_chain_kill(gs, hand, ctx)
    if action is not None:
        return action

    # 1. Lethal: any attack whose total effective damage this turn reaches a
    # specific enemy's remaining effective HP. Enemies closest to death get
    # first look so we finish weak targets off.
    attack_options = [c for c in hand if _is_attack_option(c)]
    # Serpent Form and friends damage on *any* card played, so when one is up
    # against a single enemy every card in hand is a lethal candidate.
    per_play = _per_play_bonus_vs(gs, active_enemies)
    lethal_pool = hand if per_play else attack_options
    # An enemy about to escape goes first among equals: killing anything else
    # can wait a turn, killing this cannot.
    # A leader within reach outranks its minions -- see `_leader_in_reach`.
    lethal_targets = active_enemies
    if ctx.focus_leader is not None:
        lethal_targets = [e for e in active_enemies if not _is_minion(e)]
    for enemy in sorted(lethal_targets, key=lambda e: (not _is_escaping(e), _damage_to_kill(e))):
        lethal_cards = [
            c for c in lethal_pool
            if _kills_enemy(c, enemy, shiv_bonus, hand, per_play,
                            strength, _player_damage_multiplier(gs, enemy),
                            live_enemies=all_enemies)
        ]
        # A kill that leaves a lethal hit coming is not worth the card. Set 3,
        # run 15: 6 HP, 24 Block against 33, and the last energy went on
        # Strike to finish a Tough Egg whose intent was Summon -- the kill
        # removed nothing, 9 still came through, and the Defend in hand would
        # have left us on 4. Step 1.1 has always had this check; this one did
        # not. A leader's kill is exempt: its minions leave with it.
        # Killing the last enemy standing ends the fight: nothing else swings
        # and end-of-turn HP loss never comes (Soul Fysh's Beckons -- three
        # archived turns at 3 HP would otherwise have passed up the kill).
        others_alive = any(
            e.get("entity_id") != enemy.get("entity_id") and (e.get("hp") or 0) > 0 and not _is_spent(e)
            for e in all_enemies
        )
        if lethal_cards and others_alive and not _is_leader(enemy, all_enemies):
            lethal_cards = [c for c in lethal_cards if not _dies_despite_kill(c, enemy, ctx, gs.hp)]
        # A segment killed while another is too healthy to follow just comes
        # back -- see `_kill_is_undone`. Taken anyway when the hit coming would
        # kill us: it still removes that segment's attack for two turns.
        if lethal_cards and _kill_is_undone(enemy, all_enemies) and ctx.unblocked + ctx.hp_loss < gs.hp:
            lethal_cards = []
        if lethal_cards:
            # Use the SMALLEST sufficient hit, not the biggest. Overkilling a
            # 5 HP enemy with an 11-damage card can waste the only card that
            # would also have killed the 10 HP enemy beside it -- picking
            # least-overkill (then cheapest, then avoiding Sly) leaves the
            # heavy hitters available for targets that actually need them.
            best_for_enemy = min(
                lethal_cards,
                key=lambda c: (
                    _effective_damage(c, enemy, shiv_bonus, hand,
                                      strength, damage_multiplier),
                    _cost_int(c),
                    card_db.is_sly(c),
                ),
            )
            fields: dict[str, Any] = {"card_index": best_for_enemy["index"]}
            if _needs_target(best_for_enemy):
                fields["target"] = enemy["entity_id"]
            return "play_card", fields

    action = _step_duplication(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_capped_turn(gs, hand, ctx)
    if action is not None:
        return action

    # 1.1. Kill the attacker: a threat we can finish *this turn*, even if it
    # takes several cards, is better removed than blocked -- killing it stops
    # 100% of its damage and permanently reduces the fight, whereas block is
    # spent and gone. The single-card check above misses this (two Strikes
    # into a 10 HP attacker), which is how the bot ended up blocking a hit it
    # could have deleted. Only commit when the kill actually leaves us safe:
    # whatever the *other* enemies still swing for must not take us out.
    # Leaders first: some encounters end outright when the leader dies, so
    # that kill is worth more than any amount of blocking.
    # Leader status is judged against every enemy, so a Minion anywhere on the
    # board names the enemy it follows. With that leader in reach, its
    # minions are not chased at all.
    kill_candidates = [
        e for e in active_enemies
        if (_enemy_attack_damage(e) > 0 or _is_leader(e, all_enemies) or _is_escaping(e))
        and not (ctx.focus_leader is not None and _is_minion(e))
        # A Decimillipede segment the revive would undo -- see `_kill_is_undone`.
        and not (_kill_is_undone(e, all_enemies) and ctx.unblocked + ctx.hp_loss < gs.hp)
    ]
    for enemy in sorted(
        kill_candidates,
        key=lambda e: (_is_escaping(e), _is_leader(e, all_enemies), _enemy_attack_damage(e)),
        reverse=True,
    ):
        # Several cards means several hits: a Skittish enemy not yet hit this
        # turn raises its Block after the first -- see `_skittish_pending`.
        needed = _damage_to_kill(enemy) + _skittish_pending(enemy)
        if _reachable_damage(hand, enemy, gs.energy, shiv_bonus, all_enemies) < needed:
            continue
        if not _is_leader(enemy, all_enemies):
            # Ordinary attacker: only chase the kill if what's left can't kill
            # us. For a leader we skip this -- the fight ends, so nothing else
            # gets to swing at all.
            damage_removed = _enemy_attack_damage(enemy)
            residual_unblocked = max(0, (incoming - damage_removed) - current_block)
            if residual_unblocked >= gs.hp:
                continue  # something else still kills us -- defend instead
        killer = max(
            (c for c in attack_options if _certain_on(c, all_enemies)),
            key=lambda c: (not card_db.is_sly(c), _effective_damage(c, enemy, shiv_bonus)),
            default=None,
        )
        if killer is not None:
            fields = {"card_index": killer["index"]}
            if _needs_target(killer):
                fields["target"] = enemy["entity_id"]
            return "play_card", fields

    action = _step_stun_threshold(gs, hand, ctx, attack_options)
    if action is not None:
        return action

    action = _step_powers(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_sequencing(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_free_damage(gs, hand, ctx, attack_options)
    if action is not None:
        return action

    action = _step_purge_held_status(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_mitigation(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_survive(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_value(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_damage(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_leftover_block(gs, hand, ctx)
    if action is not None:
        return action

    action = _step_final_leftover(gs, hand, ctx)
    if action is not None:
        return action

    # Nothing playable left (only unaffordable/unplayable cards remain).
    return "end_turn", {}
