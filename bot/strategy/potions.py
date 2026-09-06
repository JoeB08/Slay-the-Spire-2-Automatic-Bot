"""Potion usage during combat.

Potions are a limited resource, but hoarding them is a losing habit: an
unspent potion is worth nothing if the run ends, and the slots cap anyway.
The policy is therefore aggressive exactly where potions matter -- elites,
bosses, and any fight where we're getting low -- and stingy in ordinary
trash fights, where the same potion is better saved.

Aggression tiers:
  * Emergency (any fight): about to take lethal or near-lethal damage, or
    already critically low -- use anything that helps, immediately.
  * Elites/bosses: spend freely. These are the fights that end runs, and
    saving a potion for a hypothetical later fight is how you lose this one.
  * Low HP in any fight: treat it like an elite.
  * Trash fights at healthy HP: only use a potion to seal a kill, or when
    potion slots are full and one would otherwise be wasted.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..game_state import GameState

_DAMAGE_RE = re.compile(r"[Dd]eal (\d+) damage")
_BLOCK_RE = re.compile(r"[Gg]ain (\d+) Block")
# "Gain 7 Plating" -- defensive, but never says "Block".
_PLATING_RE = re.compile(r"[Gg]ain (\d+) (?:Plating|Metallicize|Armou?r)")
_INTENT_NUM_RE = re.compile(r"(\d+)")

_HEAL_HINTS = ("heal", " hp")
# Defensive effects are not all called "Block": Heart of Iron grants
# *Plating* ("at the end of your turn, gain Block"), and a live run let the
# bot die holding it because the emergency branch matched the literal word.
_BLOCK_HINTS = ("block", "plating", "metallicize", "intangible", "barricade", "armor", "armour")
_OFFENSIVE_HINTS = ("damage", "poison", "weak", "vulnerable", "attack")
_UTILITY_HINTS = ("draw", "energy")

_GAIN_STRENGTH_RE = re.compile(r"gain (\d+) strength", re.IGNORECASE)
_GAIN_DEXTERITY_RE = re.compile(r"gain (\d+) dexterity", re.IGNORECASE)
# "At the end of your turn, lose 5 Strength" / "...this turn" -- the buff
# evaporates, so using it outside the turn it pays off is pure waste.
_TEMPORARY_RE = re.compile(r"(this turn|end of (?:your )?turn)", re.IGNORECASE)
# "Heal 10 HP" / "Gain 8 HP" / "Restore 12 HP"
_HEAL_AMOUNT_RE = re.compile(r"(?:heal|restore|gain)\s+(\d+)\s*(?:max\s+)?hp", re.IGNORECASE)
# Don't spend a heal unless the missing HP soaks up at least this much of it.
MIN_HEAL_EFFICIENCY = 0.5

# A potion is a limited resource; drinking one should at least beat playing a
# Defend. A Weak potion against a 4-damage attack prevents 1 damage -- the
# potion is gone and the turn is unchanged. Observed live twice: "just used
# weak potion when the boss wasnt attacking" (fixed separately by
# `_accomplishes_nothing`) and a Weak potion spent on a trivial hit, which
# that check lets through because *something* is attacking.
MIN_MITIGATION_PAYOFF = 5
# Regen pays out per turn, so it wants a fight with turns left to run.
REGEN_MIN_ENEMY_HP = 60
REGEN_MAX_ROUND = 3

# Below this fraction of max HP, any fight counts as dangerous enough to spend
# potions freely, the same as an elite/boss.
LOW_HP_FRACTION = 0.5
# Below this we're in emergency territory -- burn whatever helps right now.
CRITICAL_HP_FRACTION = 0.3


def _needs_enemy_target(potion: dict[str, Any]) -> bool:
    return potion.get("target_type") in ("AnyEnemy", "Enemy")


_POTION_DROP_HINTS = ("potion",)
_PER_COMBAT_HINTS = ("combat", "battle", "encounter", "fight")
_DROP_COUNT_RE = re.compile(r"(\d+)\s+potions?", re.IGNORECASE)

# Potions at or above this value are kept even when drops are guaranteed --
# a revive is worth more than any number of ordinary potions.
RESERVE_VALUE = 100.0


def potion_drop_rate(gs: GameState) -> int:
    """How many potions our relics guarantee per combat (0 if none).

    This changes the whole calculus: with a guaranteed drop, potions are a
    *renewable* resource, so sitting on a full belt means the next drop is
    thrown away. Detected from relic text ("gain 3 potions after each
    combat") rather than a relic name list, so unfamiliar equivalents count.
    """
    total = 0
    for relic in gs.relics:
        description = (relic.get("description") or "").lower()
        if not any(h in description for h in _POTION_DROP_HINTS):
            continue
        if not any(h in description for h in _PER_COMBAT_HINTS):
            continue
        m = _DROP_COUNT_RE.search(description)
        total += int(m.group(1)) if m else 1
    return total


# Rough value tiers, keyed off effect text rather than a hardcoded name list
# so unfamiliar potions still get a sensible score. Used to decide which
# potion to throw away when the belt is full and a better one is offered.
_VALUE_RULES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("revive", "return to life", "prevent death"), 100.0),
    (("energy",), 55.0),
    (("heal", " hp"), 50.0),
    (("strength", "dexterity"), 45.0),
    (("vulnerable", "weak", "poison"), 40.0),
    (("block", "plating"), 35.0),
    (("draw",), 30.0),
)


def potion_value(potion: dict[str, Any]) -> float:
    """Higher is better. Damage potions scale with their printed damage so a
    big Fire Potion outranks a small one; everything else falls back to the
    effect-text tiers above."""
    description = (
        potion.get("description")
        or potion.get("potion_description")
        or ""
    ).lower()

    dmg = _damage_value({"description": description})
    value = 25.0 + dmg * 1.5 if dmg else 0.0

    for hints, tier in _VALUE_RULES:
        if any(h in description for h in hints):
            value = max(value, tier)

    return value or 20.0  # unknown effect -- still worth something


def _incoming_damage(enemies: list[dict[str, Any]]) -> int:
    """Delegates to combat's parser so multi-hit intents ("4x4" = 16) are
    counted here too -- this module had its own copy that read only the first
    number, so the emergency branches under-estimated danger by up to 4x on
    16% of attacks. Imported lazily to avoid a circular import."""
    from . import combat as combat_mod

    return combat_mod._incoming_damage(enemies)


def _draw_would_be_wasted(potion: dict[str, Any], gs: GameState) -> bool:
    """True for a draw potion used at the hand cap, where the cards drawn are
    immediately discarded. Potions that also do something else still count."""
    from . import combat as combat_mod

    description = (potion.get("description") or "")
    if not re.search(r"draw", description, re.IGNORECASE):
        return False
    if len(gs.hand) < combat_mod.HAND_LIMIT:
        return False
    return _damage_value(potion) == 0 and _block_value(potion) == 0


def _usable(gs: GameState) -> list[dict[str, Any]]:
    return [p for p in gs.potions if p.get("can_use_in_combat", True)]


def _with_target(potion: dict[str, Any], enemies: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {"slot": potion["slot"]}
    if _needs_enemy_target(potion) and enemies:
        fields["target"] = min(enemies, key=lambda e: e.get("hp", 0))["entity_id"]
    return fields


def _matches(potion: dict[str, Any], hints: tuple[str, ...]) -> bool:
    desc = (potion.get("description") or "").lower()
    return any(h in desc for h in hints)


def _temp_strength(potion: dict[str, Any]) -> int:
    """Strength from a potion that only lasts this turn (Flex Potion)."""
    desc = potion.get("description") or ""
    m = _GAIN_STRENGTH_RE.search(desc)
    if m and _TEMPORARY_RE.search(desc):
        return int(m.group(1))
    return 0


def _temp_dexterity(potion: dict[str, Any]) -> int:
    """Dexterity from a potion that only lasts this turn (Speed Potion)."""
    desc = potion.get("description") or ""
    m = _GAIN_DEXTERITY_RE.search(desc)
    if m and _TEMPORARY_RE.search(desc):
        return int(m.group(1))
    return 0


def is_turn_scoped_buff(potion: dict[str, Any]) -> bool:
    """True for buffs that expire at end of turn. These must be held until the
    turn they actually convert into damage or block -- spending one at the
    start of a fight "to get value from it" wastes it entirely."""
    return bool(_temp_strength(potion) or _temp_dexterity(potion))


def _affordable_cards(hand: list[dict[str, Any]], energy: int, want_block: bool) -> list[dict[str, Any]]:
    """Cards we could actually play this turn, cheapest first, within energy.
    Imported lazily: combat imports this module, so a module-level import back
    into combat would be circular."""
    from . import combat as combat_mod

    def cost(card: dict[str, Any]) -> int:
        return combat_mod._cost_int(card)

    def relevant(card: dict[str, Any]) -> bool:
        if not card.get("can_play", True):
            return False
        if want_block:
            return combat_mod._card_block(card) > 0
        return combat_mod._is_attack_option(card)

    chosen: list[dict[str, Any]] = []
    budget = energy
    for card in sorted((c for c in hand if relevant(c)), key=cost):
        if cost(card) <= budget:
            chosen.append(card)
            budget -= cost(card)
    return chosen


def _turn_scoped_buff_pays_off(
    potion: dict[str, Any], gs: GameState, enemies: list[dict[str, Any]], unblocked: int
) -> bool:
    """Only spend a one-turn buff when it changes this turn's outcome:
    Strength that turns a survivable enemy into a dead one, or Dexterity that
    turns an uncoverable hit into a covered one."""
    from . import combat as combat_mod

    hand = gs.hand
    strength = _temp_strength(potion)
    if strength:
        attacks = _affordable_cards(hand, gs.energy, want_block=False)
        if not attacks:
            return False
        shiv_bonus = combat_mod._shiv_damage_bonus(gs)
        for enemy in enemies:
            remaining = combat_mod._effective_hp(enemy)
            base = sum(combat_mod._effective_damage(c, enemy, shiv_bonus) for c in attacks)
            buffed = base + strength * len(attacks)
            if base < remaining <= buffed:
                return True  # the buff is exactly what makes this kill happen
        return False

    dexterity = _temp_dexterity(potion)
    if dexterity and unblocked > 0:
        blockers = _affordable_cards(hand, gs.energy, want_block=True)
        if not blockers:
            return False
        current_dex = combat_mod._player_dexterity(gs)
        base = sum(combat_mod._effective_block(c, current_dex) for c in blockers)
        buffed = base + dexterity * len(blockers)
        return base < unblocked <= buffed

    return False


def _damage_value(potion: dict[str, Any]) -> int:
    m = _DAMAGE_RE.search(potion.get("description", ""))
    return int(m.group(1)) if m else 0


def _block_value(potion: dict[str, Any]) -> int:
    """Defensive magnitude, counting Plating-style effects as well as Block.
    "Gain 7 Plating" is 7 points of damage prevention; matching only "Block"
    scored it 0 and left it unused while the bot died."""
    description = potion.get("description", "") or ""
    m = _BLOCK_RE.search(description)
    if m:
        return int(m.group(1))
    m = _PLATING_RE.search(description)
    return int(m.group(1)) if m else 0


def _heal_amount(potion: dict[str, Any]) -> int:
    m = _HEAL_AMOUNT_RE.search(potion.get("description", "") or "")
    if not m:
        return 0
    return int(next(g for g in m.groups() if g))


def _is_healing(potion: dict[str, Any]) -> bool:
    return _heal_amount(potion) > 0 or _matches(potion, _HEAL_HINTS)


def _healing_is_worthwhile(potion: dict[str, Any], gs: GameState) -> bool:
    """HP can't exceed max, so healing at (or near) full is thrown away.

    A live run burned a healing potion at full health for zero benefit. We
    require the missing HP to cover at least half the heal before spending it;
    with an unknown heal amount, just require *some* missing HP.
    """
    missing = gs.max_hp - gs.hp
    if missing <= 0:
        return False
    amount = _heal_amount(potion)
    if amount <= 0:
        return True  # unknown size -- any missing HP justifies it
    return missing >= amount * MIN_HEAL_EFFICIENCY


def _is_regen(potion: dict[str, Any]) -> bool:
    return "regen" in (potion.get("description") or "").lower()


def _useful_healers(potions: list[dict[str, Any]], gs: GameState) -> list[dict[str, Any]]:
    """Instant healing that isn't wasted. Regen is excluded: it trickles in
    over several turns, so it's no answer to an emergency and is handled by
    its own long-fight rule below."""
    return [
        p
        for p in potions
        if _is_healing(p) and not _is_regen(p) and _healing_is_worthwhile(p, gs)
    ]


def _fight_will_last(gs: GameState, enemies: list[dict[str, Any]]) -> bool:
    """Whether there's enough fight left for a per-turn effect to pay off.

    Regen heals a bit each turn, so its value is roughly (turns remaining x
    amount). Spending it on a nearly-dead trash pack wastes most of it, while
    an elite/boss or a big pile of remaining enemy HP gives it time to work.
    """
    total_enemy_hp = sum(e.get("hp", 0) for e in enemies)
    if gs.state_type in ("elite", "boss"):
        return True
    return total_enemy_hp >= REGEN_MIN_ENEMY_HP


def _regen_worth_using(potion: dict[str, Any], gs: GameState, enemies: list[dict[str, Any]]) -> bool:
    if gs.hp >= gs.max_hp:
        return False  # nothing to heal
    if not _fight_will_last(gs, enemies):
        return False
    # Early in the fight, so it has turns left to tick.
    round_number = gs.battle.get("round") or 1
    return round_number <= REGEN_MAX_ROUND


def _can_kill_every_attacker(gs: GameState, enemies: list[dict[str, Any]]) -> bool:
    """Whether this turn's cards can finish everything that's about to attack.

    If so, the incoming damage never lands, and spending a defensive potion on
    it is pure waste -- the bot did exactly that, drinking a block potion and
    then killing the enemy with the same turn's cards.
    """
    from . import combat as combat_mod

    attackers = [e for e in enemies if combat_mod._enemy_attack_damage(e) > 0]
    if not attackers:
        return True  # nothing is attacking; no defence needed either way

    hand = combat_mod._playable_hand(gs)
    if not hand:
        return False
    shiv_bonus = combat_mod._shiv_damage_bonus(gs)
    energy = gs.energy
    for enemy in attackers:
        reachable = combat_mod._reachable_damage(hand, enemy, energy, shiv_bonus)
        if reachable < combat_mod._damage_to_kill(enemy):
            return False
    return True


def _accomplishes_nothing(potion: dict[str, Any], gs: GameState, enemies: list[dict[str, Any]]) -> bool:
    """True for a potion whose whole effect is wasted right now.

    Weak only reduces *attack* damage. A Weak potion drunk while nothing is
    winding up reduces nothing, and Weak persists for several turns -- so
    holding it until the enemy actually attacks costs nothing and is strictly
    better. Observed live: the only potion held was spent applying 3 Weak to a
    252 HP boss on a turn it was not attacking.

    The same check also refuses a Weak potion whose reduction is trivial: at
    25% off, a 4-damage attack yields 1 prevented damage, which is not worth a
    consumable. `MIN_MITIGATION_PAYOFF` sets the bar at roughly a Defend.

    Deliberately narrow. Damage, block and healing potions are judged by the
    existing aggression tiers, which live evidence says are about right.
    """
    from . import combat as combat_mod

    description = (potion.get("description") or "").lower()
    if "weak" not in description:
        return False
    if _damage_value(potion) or _block_value(potion) or _heal_amount(potion):
        return False  # it does something else too
    live = [e for e in enemies if e.get("hp", 0) > 0]
    if not live:
        return False
    incoming = sum(combat_mod._enemy_attack_damage(e) for e in live)
    if incoming <= 0:
        return True  # nothing winding up: Weak reduces nothing at all
    # Something is attacking, but not necessarily enough to be worth a potion.
    prevented = incoming * combat_mod.WEAK_DAMAGE_REDUCTION
    return prevented < MIN_MITIGATION_PAYOFF


def _buff_would_convert(potion: dict[str, Any], gs: GameState) -> bool:
    """Whether a turn-scoped buff will actually turn into something this turn.

    Speed Potion is "Gain 5 Dexterity. At the end of your turn, lose 5
    Dexterity" -- worth nothing unless a Block card is played before the turn
    ends. Observed live: drunk at 1 energy with two Defends in hand, then the
    energy went to a Strike and all 5 Dexterity expired unused, while 3
    unblocked damage landed at 27 HP.

    Dexterity needs an affordable Block card; Strength needs an affordable
    attack. "Affordable" matters -- holding the card is no use if the energy
    to play it is already gone.
    """
    from . import combat as combat_mod

    hand = combat_mod._playable_hand(gs)
    if not hand:
        return False
    affordable = [c for c in hand if combat_mod._cost_int(c) <= gs.energy]
    if _temp_dexterity(potion):
        return any(combat_mod._card_block(c) > 0 for c in affordable)
    if _temp_strength(potion):
        return any(combat_mod._is_attack_option(c) for c in affordable)
    return True


def _duplication_enables_finish(potion: dict[str, Any], gs: GameState,
                                enemies: list[dict[str, Any]]) -> bool:
    """Would doubling our best card this turn win or fully cover the turn?

    The Duplicator potion ("This turn, your next card is played an extra
    time") is worth drinking exactly when the doubled card closes something
    out: a kill we could not otherwise reach, or a Block wall that covers the
    whole incoming hit. Outside those cases it is better saved.
    """
    from . import combat as combat_mod

    text = (potion.get("description") or potion.get("text") or "")
    if not combat_mod._DUPLICATOR_RE.search(text):
        return False

    hand = combat_mod._playable_hand(gs)
    if not hand:
        return False
    shiv = combat_mod._shiv_damage_bonus(gs)
    strength = combat_mod._player_strength(gs)
    dexterity = combat_mod._player_dexterity(gs)

    # A kill that only the doubling reaches.
    for enemy in enemies:
        if combat_mod._effective_hp(enemy) <= 0:
            continue
        single = any(
            combat_mod._kills_enemy(c, enemy, shiv, hand, 0, strength, 1.0) for c in hand
        )
        doubled = any(
            combat_mod._kills_enemy(c, enemy, shiv, hand, 0, strength, 2.0) for c in hand
        )
        if doubled and not single:
            return True

    # ...or a Block wall that covers the whole hit when a single card cannot.
    incoming = combat_mod._mitigated_incoming(gs, enemies, _incoming_damage(enemies))
    unblocked = max(0, incoming - gs.player.get("block", 0))
    if unblocked > 0:
        best_block = max(
            (combat_mod._effective_block(c, dexterity) for c in hand), default=0
        )
        if best_block > 0 and best_block < unblocked <= best_block * 2:
            return True
    return False


# How threatening a turn has to be before a potion with no matching need is
# worth burning purely to free a slot. A quarter of our max HP arriving is a
# real turn; 8 damage in an act 1 trash fight is not.
BURN_THREAT_FRACTION = 0.25


def _earns_its_slot(potion: dict[str, Any], gs: GameState,
                    enemies: list[dict[str, Any]], unblocked: int,
                    is_elite_or_boss: bool) -> bool:
    """Would drinking this *now* actually accomplish something?

    Used to gate the slot-pressure branches. Guaranteed potion drops (Petrified
    Toad and friends) make `would_overflow` permanently true once the belt is
    full, so without this the bot burns a potion on whatever turn it happens to
    be having -- a live run drank Liquid Memories at 21 HP against an 8-damage
    trash monster with a full hand, purely to make room.

    The pressure does not expire: it is still there next turn and next fight,
    so waiting for a turn where the potion does something costs nothing.
    """
    if _damage_value(potion) > 0:
        return True  # damage always advances the fight
    if (_block_value(potion) > 0 or _matches(potion, _BLOCK_HINTS)) and unblocked > 0:
        return True
    if _is_healing(potion) and _useful_healers([potion], gs):
        return True
    if _is_regen(potion) and _regen_worth_using(potion, gs, enemies):
        return True
    # Anything else (utility, unrecognised) only in a fight that matters, or a
    # turn taking a genuinely heavy hit.
    if is_elite_or_boss:
        return True
    return unblocked >= gs.max_hp * BURN_THREAT_FRACTION


def suggest_potion_use(gs: GameState) -> Optional[tuple[str, dict[str, Any]]]:
    enemies = gs.enemies
    potions = _usable(gs)
    if not enemies or not potions:
        return None

    # If the cards in hand can kill everything that's about to swing, the
    # threat is already handled -- don't spend a potion defending against
    # damage that will never arrive.
    threat_is_handled = _can_kill_every_attacker(gs, enemies)

    # Duplication is worth drinking the moment it closes out the turn -- a
    # kill we cannot otherwise reach, or a Block wall that covers the hit.
    for potion in potions:
        if _duplication_enables_finish(potion, gs, enemies):
            return "use_potion", _with_target(potion, enemies)
    # Drop potions that would accomplish nothing this turn before any of the
    # aggression tiers get a chance to spend them.
    potions = [p for p in potions if not _accomplishes_nothing(p, gs, enemies)]
    # A draw potion at the hand cap discards everything it draws. This was
    # only checked in the elite/boss utility branch, so every other path --
    # the emergency last-ditch, slot pressure, overflow -- could still drink a
    # Swift Potion ("Draw 3 cards") into a full hand. Filtering globally means
    # no branch can spend one for nothing.
    potions = [p for p in potions if not _draw_would_be_wasted(p, gs)]
    # A buff that expires at end of turn is only worth drinking if this turn
    # can actually cash it in.
    potions = [
        p for p in potions
        if not is_turn_scoped_buff(p) or _buff_would_convert(p, gs)
    ]
    if not potions:
        return None

    hp_fraction = gs.hp_pct
    # Use combat's mitigation rather than a naive intent total: Intangible
    # caps every hit at 1 and Plating hands us block for free, so the raw
    # number badly overstates the threat. Without this, potions saw a
    # 40-damage swing under Intangible as lethal and burned a potion to
    # "survive" a hit that would have landed for 1. Imported lazily -- combat
    # imports this module.
    from . import combat as combat_mod

    current_block = gs.player.get("block", 0) + combat_mod._pending_block_from_status(gs)
    incoming = combat_mod._mitigated_incoming(gs, enemies, _incoming_damage(enemies))
    unblocked = max(0, incoming - current_block)

    # One-turn buffs are handled separately below -- they must never be swept
    # up by the "spend freely" branches, which would burn them on turn 1 of an
    # elite for nothing.
    turn_scoped = [p for p in potions if is_turn_scoped_buff(p)]
    potions = [p for p in potions if not is_turn_scoped_buff(p)]

    for potion in turn_scoped:
        if _turn_scoped_buff_pays_off(potion, gs, enemies, unblocked):
            return "use_potion", _with_target(potion, enemies)

    is_elite_or_boss = gs.state_type in ("elite", "boss")
    is_low = hp_fraction < LOW_HP_FRACTION
    is_critical = hp_fraction < CRITICAL_HP_FRACTION
    about_to_die = unblocked >= gs.hp
    nearly_lethal = unblocked >= gs.hp * 0.5
    slots_full = len(gs.potions) >= gs.max_potion_slots

    # Guaranteed drops make potions renewable: whatever we're still holding
    # when the next batch lands is simply lost to overflow. Spend down to the
    # level that leaves room for the incoming drop.
    drop_rate = potion_drop_rate(gs)
    would_overflow = drop_rate > 0 and len(gs.potions) + drop_rate > gs.max_potion_slots

    # A fight worth spending on: the ones that actually end runs.
    high_stakes = is_elite_or_boss or is_low

    # 1. Emergency -- about to die, or already critical. Block first (it stops
    # the hit outright), then healing, then anything at all.
    # `threat_is_handled` gates the defensive branches: if our cards kill
    # every attacker this turn, the damage never lands.
    if (about_to_die or is_critical) and not threat_is_handled:
        blockers = [p for p in potions if _block_value(p) > 0 or _matches(p, _BLOCK_HINTS)]
        if blockers and unblocked > 0:
            best = max(blockers, key=_block_value)
            return "use_potion", _with_target(best, enemies)
        healers = _useful_healers(potions, gs)
        if healers:
            return "use_potion", _with_target(healers[0], enemies)
        if about_to_die:
            # Nothing defensive -- try to kill our way out.
            damagers = [p for p in potions if _damage_value(p) > 0]
            if damagers:
                best = max(damagers, key=_damage_value)
                return "use_potion", _with_target(best, enemies)
            # Last ditch: use *anything* rather than die holding it. The
            # category matchers only recognise block/heal/damage, so an
            # unfamiliar potion was never even considered -- a real death at
            # 3 HP ended the turn holding Liquid Memories ("Put a card from
            # your Discard Pile into your Hand. It's free to play this
            # turn."), which could have retrieved a Defend and played it free.
            # An unrecognised effect is a chance; an unused potion is none.
            if potions:
                return "use_potion", _with_target(potions[0], enemies)

    # 1.5. Regen: value is (turns remaining x amount), so it wants to go in
    # early on a fight that will actually run long. Held back in short trash
    # fights where most of the healing would never tick.
    for potion in potions:
        if _is_regen(potion) and _regen_worth_using(potion, gs, enemies):
            return "use_potion", _with_target(potion, enemies)

    # 2. Seal a kill in any fight -- damage that finishes an enemy now.
    for potion in potions:
        dmg = _damage_value(potion)
        if dmg and any(e.get("hp", 999) <= dmg for e in enemies):
            return "use_potion", _with_target(potion, enemies)

    # 3a. Elite/boss: the fights that actually end runs. Spend everything --
    # offense, defense, even utility. A potion saved for "later" is worth
    # nothing if this is the fight that kills us.
    if is_elite_or_boss:
        offensive = [p for p in potions if _damage_value(p) > 0 or _matches(p, _OFFENSIVE_HINTS)]
        if offensive:
            best = max(offensive, key=_damage_value)
            return "use_potion", _with_target(best, enemies)

        if unblocked > 0:
            blockers = [p for p in potions if _block_value(p) > 0 or _matches(p, _BLOCK_HINTS)]
            if blockers:
                best = max(blockers, key=_block_value)
                return "use_potion", _with_target(best, enemies)

        if is_low:
            healers = _useful_healers(potions, gs)
            if healers:
                return "use_potion", _with_target(healers[0], enemies)

        # A draw potion into a full hand is thrown away -- same trap as
        # playing a draw card at the hand cap.
        utility = [
            p for p in potions
            if _matches(p, _UTILITY_HINTS) and not _draw_would_be_wasted(p, gs)
        ]
        if utility:
            return "use_potion", _with_target(utility[0], enemies)

    # 3b. Low HP in an ordinary fight: reach for defense and healing, but not
    # utility -- drawing extra cards is not an answer to being low, and the
    # potion is better kept for an elite.
    elif is_low:
        if unblocked > 0:
            blockers = [p for p in potions if _block_value(p) > 0 or _matches(p, _BLOCK_HINTS)]
            if blockers:
                best = max(blockers, key=_block_value)
                return "use_potion", _with_target(best, enemies)

        healers = _useful_healers(potions, gs)
        if healers:
            return "use_potion", _with_target(healers[0], enemies)

    # 4. Taking a serious hit even in a normal fight -- don't just eat it.
    if nearly_lethal and unblocked > 0 and not threat_is_handled:
        blockers = [p for p in potions if _block_value(p) > 0 or _matches(p, _BLOCK_HINTS)]
        if blockers:
            best = max(blockers, key=_block_value)
            return "use_potion", _with_target(best, enemies)

    # 5. Guaranteed drops incoming and no room for them -- spend now or the
    # drop is thrown away. Burn the *least* valuable potion, and never a
    # reserve-tier one (a revive is worth more than any refill).
    if would_overflow:
        # Still don't burn a heal that would do nothing -- overflowing a
        # useless potion loses nothing, but using it at full HP is no better.
        spendable = [
            p
            for p in potions
            if potion_value(p) < RESERVE_VALUE
            and not (_is_healing(p) and gs.hp >= gs.max_hp)
            and _earns_its_slot(p, gs, enemies, unblocked, is_elite_or_boss)
        ]
        if spendable:
            best = min(spendable, key=potion_value)
            return "use_potion", _with_target(best, enemies)

    # 6. Slots are full, so the next reward would be wasted anyway -- cash the
    # least useful potion in rather than decline a new one later.
    if slots_full:
        offensive = [
            p for p in potions
            if _damage_value(p) > 0
            and _earns_its_slot(p, gs, enemies, unblocked, is_elite_or_boss)
        ]
        if offensive:
            best = min(offensive, key=_damage_value)
            return "use_potion", _with_target(best, enemies)

    return None
