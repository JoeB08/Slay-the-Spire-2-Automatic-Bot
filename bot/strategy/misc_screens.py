"""Handlers for the less common screens: treasure chests, in-combat forced
card selection (discard/exhaust/copy prompts), the card_select overlay,
bundle picks, and the Crystal Sphere minigame.

These are the least-verified parts of the bot (no live fixture captured for
several of them -- see README "Known verification gaps"). Each degrades to a
safe, loggable default rather than raising, so a bad guess here stalls one
decision instead of crashing the run; the log line makes it easy to spot and
correct once we see it happen for real.
"""
from __future__ import annotations

import re
from typing import Any

from .. import deck_memory
from ..game_state import GameState
from . import cards as card_db


# An empty relic list on a treasure screen does not mean the chest is empty --
# it usually means the payload has not populated yet. Both cases look
# identical, so try the claim a few times before walking away.
#
# A live run showed exactly why this matters. At floor 10 the first `proceed`
# happened to be *rejected* by the game, which bought another poll; by then
# the relic was listed and Bronze Scales was claimed. At floor 26 the same
# premature `proceed` was accepted and the chest closed unopened -- the relic
# count went into that floor at 8 and came out at 8.
TREASURE_CLAIM_ATTEMPTS = 3
_treasure_attempts: dict[tuple[int, int], int] = {}


def decide_treasure(gs: GameState) -> tuple[str, dict[str, Any]]:
    treasure = gs.treasure
    relics = treasure.get("relics") or treasure.get("items") or []
    if relics:
        _treasure_attempts.pop((gs.act, gs.floor), None)
        return "claim_treasure_relic", {"index": relics[0].get("index", 0)}

    # Nothing listed. Try claiming anyway -- a chest that really is empty
    # rejects it harmlessly, while one that simply had not loaded gives us the
    # relic. Bounded so a genuinely empty screen cannot trap the bot.
    key = (gs.act, gs.floor)
    tries = _treasure_attempts.get(key, 0)
    if tries < TREASURE_CLAIM_ATTEMPTS:
        _treasure_attempts[key] = tries + 1
        return "claim_treasure_relic", {"index": 0}

    _treasure_attempts.pop(key, None)
    return "proceed", {}


# The two starter cards a Silent deck cuts from. Removing every copy of one
# of them leaves the deck unable to do half of what it needs to do.
_STARTER_PAIR = ("Strike", "Defend")

# Damage an unaffordable attack must promise before it is worth keeping over
# cards we can actually play. A Strike is 6; this is set above that so only
# genuinely big cards are protected.
UNAFFORDABLE_KEEP_DAMAGE = 8


def _balanced_worst(
    remaining: list[dict[str, Any]],
    deck_names: list[str],
    already_names: list[str],
) -> dict[str, Any]:
    """The worst card, without stripping one starter type entirely.

    On a "Choose 4 cards to Remove." screen the bot removed **all four basic
    Defends**, leaving four Strikes and a single Defend+ as the deck's only
    basic block. Each pick was individually defensible -- `removal_priority`
    just ranked Defend above Strike and nothing tracked the shape of the
    result across the four picks.

    So once we have already cut more of one starter than the other, take the
    other next. Over four picks that yields 2 Strikes and 2 Defends, which is
    what a human would do.
    """
    ranked = sorted(
        remaining, key=lambda c: card_db.removal_priority(c, deck_names), reverse=True
    )
    best = ranked[0]
    base = card_db.base_name(best.get("name", "") or "")
    if base not in _STARTER_PAIR:
        return best
    other = "Defend" if base == "Strike" else "Strike"
    taken_this = sum(1 for n in already_names if card_db.base_name(n) == base)
    taken_other = sum(1 for n in already_names if card_db.base_name(n) == other)
    if taken_this > taken_other:
        alt = next(
            (c for c in ranked if card_db.base_name(c.get("name", "") or "") == other),
            None,
        )
        if alt is not None:
            return alt
    return best


def _pick_worst(hand_cards: list[dict[str, Any]], deck_names: list[str]) -> dict[str, Any]:
    """The card we'd most like to be rid of.

    Uses `removal_priority` rather than inverse pick-rate: starter
    Strikes/Defends are the prime cuts for a Silent deck, but they out-score
    genuinely weak cards on pick rate, so a naive "lowest score" pick kept the
    Strikes and removed the real cards.
    """
    return max(hand_cards, key=lambda c: card_db.removal_priority(c, deck_names))


def _pick_best(hand_cards: list[dict[str, Any]], deck_names: list[str]) -> dict[str, Any]:
    counts = card_db.deck_tag_counts(deck_names)
    return max(hand_cards, key=lambda c: card_db.score_card(c.get("name", ""), counts))


# Quest cards ("Spoils Map") are unplayable too, so in combat they are
# dead weight and fine to pitch -- `removal_priority` separately makes sure
# they are never removed from the deck.
DEAD_CARD_TYPES = {"curse", "status", "quest"}


def _is_dead_weight(card: dict[str, Any]) -> bool:
    """Cards that can't do anything for us this combat."""
    if (card.get("type") or "").lower() in DEAD_CARD_TYPES:
        return True
    return card.get("can_play") is False


def _is_ethereal(card: dict[str, Any]) -> bool:
    """Ethereal: "if this card is in your hand at the end of your turn, it is
    Exhausted."

    This inverts the usual instinct for junk. Discarding an Ethereal curse
    just sends it to the discard pile to be drawn again later; *holding* it
    lets it exhaust itself, deleting it for the rest of the combat. So an
    Ethereal curse is one of the last things we want to discard, not the
    first.
    """
    for kw in card.get("keywords") or []:
        if (kw.get("name") or "").strip().lower() == "ethereal":
            return True
    return "ethereal" in (card.get("description") or "").lower()


def _cost_of(card: dict[str, Any]) -> int:
    try:
        return int(card.get("cost"))
    except (TypeError, ValueError):
        return 0  # X-cost / unparsable -- treat as cheap so it isn't dumped for cost alone


_UP_TO_RE = re.compile(r"up to (\d+)", re.IGNORECASE)


def _optional_pick_limit(prompt: str) -> int:
    """How many cards we want on a prompt that lets us confirm with none.

    `can_confirm` reports that confirming is *legal*, not that we have picked
    enough. On an optional prompt it is true from the moment the screen opens,
    so checking it first meant the bot confirmed instantly, every time: one
    logged run offered "Choose a card to Retain." 41 times and retained
    nothing on all 41. Only Retain is treated as opt-in this way -- every
    other optional prompt keeps the old behaviour.
    """
    if "retain" not in prompt:
        return 0
    m = _UP_TO_RE.search(prompt)
    return int(m.group(1)) if m else 1


def _pick_retain(
    hand_cards: list[dict[str, Any]], deck_names: list[str]
) -> dict[str, Any] | None:
    """The card most worth carrying into next turn, or None if there is none.

    Retaining costs nothing -- an unretained card is discarded regardless, and
    Sly triggers only on a discard *before* end of turn, so a retained Sly
    card keeps its free play for a turn we control. But not every card earns
    the slot: Curses and Status would only clog next turn's hand, and an
    Ethereal card exhausts itself at end of turn whether retained or not.

    Ranking deliberately does *not* lead with `score_card`, which answers
    "should this card be in the deck" -- a question retain isn't asking. On
    that scale a starter Defend (30) outranks Nightmare (28), which is
    backwards here: the deck is full of Defends and one will come round again
    next turn, while the expensive card is exactly the one worth guaranteeing.
    So: skip starters, then take the most expensive, then a Sly card over a
    plain one, and only then fall back to deck score.
    """
    counts = card_db.deck_tag_counts(deck_names)
    usable = [c for c in hand_cards if not _is_dead_weight(c) and not _is_ethereal(c)]
    if not usable:
        return None
    return max(
        usable,
        key=lambda c: (
            0 if card_db.base_name(c.get("name", "")) in card_db.STARTERS_BY_NAME else 1,
            _cost_of(c),
            1 if card_db.is_sly(c) else 0,
            card_db.score_card(c.get("name", ""), counts),
        ),
    )


def _has_retain(card: dict[str, Any]) -> bool:
    """True for cards that survive the end-of-turn discard.

    This inverts which card is cheaper to throw away. A card *without* Retain
    is discarded at end of turn regardless, so pitching it to pay a discard
    cost loses nothing extra; a Retain card would have carried into next turn
    for free, so pitching that one is the only choice that destroys value.
    """
    for kw in card.get("keywords") or []:
        if (kw.get("name") or "").strip().lower() == "retain":
            return True
    text = (card.get("description") or card.get("text") or "")
    return bool(re.match(r"\s*retain\b", text, re.IGNORECASE))


def _sly_realised_value(
    card: dict[str, Any],
    enemies: list[dict[str, Any]] | None,
    shiv_bonus: int,
    unblocked: int,
    dexterity: int,
) -> int:
    """What a Sly card's free play would actually accomplish this turn.

    Discarding a Sly card plays it for free, so the pitch is worth exactly
    what that play does right now -- damage the enemy's Block eats is worth
    nothing, and Block beyond the incoming hit is worth nothing either. Both
    are capped here so the two are directly comparable.
    """
    from . import combat as combat_mod

    value = 0

    block = combat_mod._effective_block(card, dexterity)
    if block > 0:
        value = max(value, min(block, unblocked))

    if enemies:
        for enemy in enemies:
            # Poison ignores Block entirely, so it lands whatever the wall --
            # score it separately from the part Block can absorb.
            poison = combat_mod._card_poison(card)
            attack = combat_mod._effective_damage(card, enemy, shiv_bonus) - poison
            landed = max(0.0, attack - combat_mod._enemy_block(enemy))
            value = max(value, int(min(landed + poison, combat_mod._damage_to_kill(enemy))))
    else:
        value = max(value, combat_mod._card_damage(card) + combat_mod._card_poison(card))

    return value


def _self_exhausts(card: dict[str, Any]) -> bool:
    """Can we get rid of this card for good just by playing it?

    Only counts when the game says it is playable -- an Unplayable Status
    mentioning Exhaust (Infection) can never be removed this way, and
    discarding really is its only outlet.
    """
    if card.get("can_play") is False:
        return False
    return "exhaust" in (card.get("description") or card.get("text") or "").lower()


def _discard_rank(
    card: dict[str, Any],
    counts: dict[str, int],
    energy: int,
    enemies: list[dict[str, Any]] | None = None,
    shiv_bonus: int = 0,
    unblocked: int = 0,
    dexterity: int = 0,
    hand: list[dict[str, Any]] | None = None,
) -> tuple:
    """Higher = better to throw away. Explicit tiers, most-discardable first:

       4  Damages us while held (Infection) -- a recurring HP cost every turn
          it stays in hand, so getting rid of it beats a one-off free play.
       3  Sly -- discarding *plays it for free*, the single best pitch.
       2  Dead weight -- Curses/Status/unplayable, useless all combat.
          (Ethereal dead weight drops to tier 0: holding it exhausts it away
          for good, while discarding only recycles it back into the deck.)
       1  Surplus block (already fully covered) or a card we can't afford.
       0  Ordinary cards, worst first.
      -1  Affordable attacks -- they convert to damage this turn.
      -2  Block we actually need this turn -- pitching it to keep an attack
          is how you die; only a fight-ending attack outranks it.
      -3  An attack that is lethal right now -- never pitch the kill.

    Tiers are explicit rather than score-derived: two cards landing in the
    same tier previously fell back to a score comparison that happened to
    favour whichever appeared first, which is how a needed Defend got pitched
    to keep a non-lethal Strike.
    """
    from . import combat as combat_mod  # deferred: combat imports this module

    score = card_db.score_card(card.get("name", ""), counts)
    affordable = _cost_of(card) <= energy
    # Higher = more discardable, so a card WITHOUT Retain sorts above one with
    # it. Used as the final tie-break in every tier.
    retain_rank = 0 if _has_retain(card) else 1
    is_attack = combat_mod._is_attack_option(card)
    is_block = combat_mod._card_block(card) > 0 and not is_attack

    # Cards that damage us every turn they're held (Infection: "take 3
    # damage") are the most urgent thing to be rid of -- holding one is a
    # recurring HP cost, worse than a one-off missed free Sly play.
    # Never pitch the answer to a death clock, whatever else it looks like.
    clock = combat_mod._death_clock(enemies or [])
    if clock and combat_mod._delays_death_clock(card, clock[0]):
        return (-4, 0, retain_rank)

    if combat_mod._self_damage_in_hand(card) > 0 and not _is_ethereal(card):
        # Spend the discard on the one we have no other way to remove.
        # Beckon (6 HP) is `can_play: True`, so a single energy deletes it;
        # Infection (3 damage) is Unplayable, and discarding is its *only*
        # outlet. Ranking on magnitude alone pitched the Beckon and kept the
        # Infection -- the exact opposite, and it wastes the purge step that
        # would otherwise have handled Beckon for free.
        cannot_purge = 0 if card.get("can_play") is True else 1
        return (4, cannot_purge, combat_mod._self_damage_in_hand(card), retain_rank)

    if card_db.is_sly(card):
        # Among Sly cards, pitch the one whose free play does the most *this
        # turn* -- not the one with the best pick rate. A live run discarded a
        # Sly attack the enemy's Block absorbed entirely while holding a Sly
        # block card that would have covered the incoming hit outright: both
        # landed in this tier and the tie-break knew nothing about the board.
        return (3, _sly_realised_value(card, enemies, shiv_bonus, unblocked, dexterity), score)

    if _is_dead_weight(card):
        if _is_ethereal(card):
            # Hold it: at end of turn it exhausts itself and is gone for the
            # combat. Discarding would only recycle it back into the deck.
            return (0, -score, retain_rank)
        # Same reasoning, one step further: a dead card we can *play* to
        # exhaust it (Slimed: "Draw 1 card. Exhaust.") is gone for good once
        # played, while discarding it only shuffles it back for next time.
        # Spend the discard on the dead weight we have no other way to
        # remove, and exhaust this one ourselves with spare energy.
        if _self_exhausts(card):
            return (1, -score, retain_rank)
        return (2, -score, retain_rank)

    if is_attack and affordable and enemies:
        for enemy in enemies:
            if combat_mod._kills_enemy(card, enemy, shiv_bonus):
                return (-3, -score, retain_rank)

    # A card that blocks *and* attacks (Cloak and Dagger: "Gain 6 Block. Add 1
    # Shiv") used to be classed attack-only, so its Block was invisible here
    # and it was pitched ahead of a plain 5-Block Defend. Block counts whether
    # or not the card also attacks; the tie-break is what the card is actually
    # worth this turn, so the least useful one goes.
    block_value = combat_mod._effective_block(card, dexterity)
    if block_value > 0:
        # Only block the turn actually needs is protected. With three block
        # cards against 5 unblocked damage, two are surplus -- and treating
        # every one of them as precious is how a Retain Snakebite (7 poison)
        # got pitched to keep a third redundant Defend.
        other_block = 0
        if hand is not None:
            # Only Block we can actually PLAY counts as cover. Summing every
            # Block card in hand regardless of cost meant an unaffordable Dash
            # (10 Block for 2 energy, with 1 energy left) read as "someone else
            # covers it" and the affordable Defend got pitched -- discarding
            # Block on a turn that needed it. Reported live.
            other_block = sum(
                combat_mod._effective_block(c, dexterity)
                for c in hand
                if c.get("index") != card.get("index")
                and _cost_of(c) <= energy
            )
        surplus = other_block >= unblocked > 0
        if surplus and not is_attack:
            return (1, -score, retain_rank)  # someone else already covers it
        if unblocked <= 0 and not is_attack:
            return (1, -score, retain_rank)  # covered already -- keep energy for attacks
        if affordable and unblocked > 0:
            target = enemies[0] if enemies else None
            damage_part = (
                combat_mod._effective_damage(card, target, shiv_bonus, hand)
                if target is not None
                else combat_mod._card_damage(card)
            )
            realised = min(block_value, unblocked) + damage_part
            return (-2, -realised, retain_rank)

    # Unaffordable *this turn* is not the same as worthless. Up My Sleeve
    # ("Add 3 Shivs into your Hand", 2 energy) is 12 damage the moment we can
    # pay for it -- and its own text reduces its cost by 1 -- but with 1
    # energy it fell into the "can't afford it" tier and was pitched ahead of
    # two 6-damage Strikes, throwing away the lethal it would have set up.
    #
    # Rank it in the same tier as the affordable attacks and let raw damage
    # decide, so the 12-damage card survives and a 6-damage Strike goes
    # instead. Putting it a tier *above* them would still pitch it first,
    # which is the bug.
    if is_attack and not affordable:
        target = enemies[0] if enemies else None
        worth = (
            combat_mod._effective_damage(card, target, shiv_bonus, hand)
            if target is not None
            else combat_mod._card_damage(card)
        )
        if worth >= UNAFFORDABLE_KEEP_DAMAGE:
            return (-1, -worth, retain_rank)

    if is_attack and affordable:
        # Rank attacks by the damage they'd actually deal, not by pick rate.
        # A live run discarded Dagger Spray ("4 damage to ALL enemies twice"
        # = 8) to keep a 6-damage Strike, because both landed in this tier and
        # the tie-break was card *score*.
        # `hand` matters: Flechettes reads "Deal 5 damage for each Skill in
        # your Hand", so without it the card is valued at its printed 5
        # instead of the 20 it would actually deal. Observed live -- it was
        # pitched to pay Survivor's discard cost with four Skills in hand.
        target = enemies[0] if enemies else None
        output = (
            combat_mod._effective_damage(card, target, shiv_bonus, hand)
            if target is not None
            else combat_mod._card_damage(card) + combat_mod._card_poison(card)
        )
        # Damage the card *prevents* counts as much as damage it deals.
        # Neutralize ("Deal 2 damage. Apply 1 Weak") was ranked on its 2 and
        # pitched as the weakest card in hand, while its Weak was worth ~3
        # against the 12-damage attacker it was facing.
        if enemies:
            output += combat_mod._mitigation_value(card, enemies)
        # A free card is cheap to keep, but crediting that with a flat bonus
        # was tried and reverted: at +4 it pushed a 2-damage Neutralize above
        # a 7-damage AoE, so the ranker pitched the best card in hand. The
        # real limitation is that tiers are categorical -- an attack is always
        # more discardable than "needed" block, however marginal that block
        # is -- and that wants a considered fix, not another constant.
        return (-1, -output, retain_rank)

    if not affordable:
        return (1, -score, retain_rank)
    return (0, -score, retain_rank)


def decide_hand_select(gs: GameState) -> tuple[str, dict[str, Any]]:
    """A card effect is asking us to choose card(s) from hand -- e.g. a forced
    discard/exhaust (pick our weakest) vs. a copy/upgrade-style pick (pick our
    strongest). We infer which from the screen's own prompt text. The offered
    cards/current selection live under `hand_select`, not the main `hand` --
    and `can_confirm` is the authoritative signal for when to stop selecting."""
    hs = gs.raw.get("hand_select") or {}
    offered = hs.get("cards") or []
    already = {c["index"] for c in (hs.get("selected_cards") or [])}
    remaining = [c for c in offered if c["index"] not in already]
    prompt = (hs.get("prompt") or "").lower()

    # Confirm once we've picked enough -- which on an opt-in prompt is not the
    # moment the screen opens. See `_optional_pick_limit`.
    if hs.get("can_confirm") and len(already) >= _optional_pick_limit(prompt):
        return "combat_confirm_selection", {}
    if not remaining:
        return "combat_confirm_selection", {}

    deck_names = deck_memory.current_deck(gs)

    if "discard" in prompt:
        from . import combat as combat_mod

        counts = card_db.deck_tag_counts(deck_names)
        shiv_bonus = combat_mod._shiv_damage_bonus(gs)
        raw_incoming = combat_mod._incoming_damage(gs.enemies) + combat_mod._hand_curse_damage(gs.hand)
        incoming = combat_mod._mitigated_incoming(gs, gs.enemies, raw_incoming)
        covered = gs.player.get("block", 0) + combat_mod._pending_block_from_status(gs)
        unblocked = max(0, incoming - covered)
        dexterity = combat_mod._player_dexterity(gs)
        chosen = max(
            remaining,
            key=lambda c: _discard_rank(
                c, counts, gs.energy, gs.enemies, shiv_bonus, unblocked, dexterity, remaining
            ),
        )
    elif "retain" in prompt:
        retained = _pick_retain(remaining, deck_names)
        if retained is None:
            # Nothing but Curses/Status/Ethereal on offer -- retaining any of
            # them just carries the clog into next turn.
            return "combat_confirm_selection", {}
        chosen = retained
    elif "exhaust" in prompt:
        # Exhaust removes the card outright -- Sly gets no trigger here, so
        # it's a plain "lose our worst card" choice. Ethereal cards are the
        # exception: they exhaust themselves at end of turn anyway, so
        # spending the effect on one wastes it. Prefer non-Ethereal targets.
        non_ethereal = [c for c in remaining if not _is_ethereal(c)]
        chosen = _pick_worst(non_ethereal or remaining, deck_names)
    else:
        chosen = _pick_best(remaining, deck_names)
    return "combat_select_card", {"card_index": chosen["index"]}


def _removal_returns_upgraded(gs: GameState) -> bool:
    """True when a relic turns card removal into an upgrade ("remove a card,
    add it back upgraded").

    With that relic the removal screen is no longer a thinning decision, so
    the usual targets invert: feeding it a starter Strike wastes the effect.
    Detected from relic text so an unfamiliar equivalent still counts.
    """
    for relic in gs.relics:
        description = (relic.get("description") or "").lower()
        if "upgrad" in description and ("remov" in description or "transform" in description):
            return True
    return False


def _card_select_signature(cs: dict[str, Any]) -> tuple:
    """Identifies one visit to a card_select screen, so our own record of what
    we've picked resets when a different screen appears."""
    return (
        cs.get("screen_type"),
        cs.get("prompt"),
        tuple((c.get("id"), c.get("index")) for c in (cs.get("cards") or [])),
    )


# card_select, unlike hand_select, reports no `selected_cards`, so there is no
# way to read back what we've already picked. Multi-pick prompts ("Choose 2
# Common Cards to Add to Your Deck") therefore need us to remember it: without
# this the bot re-picked its single best card forever (81 identical
# `select_card {'index': 4}` calls in one live run) and never reached the
# confirm step.
_card_select_visit: dict[str, Any] = {
    "signature": None,
    "picked": set(),
    "confirms": 0,
}

# How many times to confirm the same unchanged screen before concluding the
# prompt cannot be satisfied and leaving instead.
#
# Gnarled Hammer's "Choose 3 cards to Enchant." is the case this exists for.
# The bot selected three cards and confirmed, the API returned `ok`, and the
# screen never advanced -- then, because the visit state was cleared on every
# confirm, it re-selected the same three from scratch. If `select_card`
# toggles (the payload never populates `selected`, so we cannot tell), that
# turns the three selections back OFF, so the next confirm fails too. It
# alternated 32 times, tripped the freeze detector 14 times, and the recovery
# killed and relaunched the game each time until a human intervened.
MAX_CONFIRM_ATTEMPTS = 3


_CHOOSE_N_RE = re.compile(r"choose\s+(\d+)", re.IGNORECASE)


def _required_picks(prompt: str) -> int:
    """How many cards a card_select prompt actually wants.

    `can_confirm` reports that confirming is *legal*, not that enough is
    selected -- the same trap as the Retain prompt. "Choose 3 cards to
    Enchant." arrives with `can_confirm: True` and nothing selected, so
    confirming immediately sent 0 of 3, the game ignored it, the state never
    changed, and the stuck detector relaunched the game in a loop.
    """
    m = _CHOOSE_N_RE.search(prompt or "")
    return int(m.group(1)) if m else 1


def decide_card_select(gs: GameState) -> tuple[str, dict[str, Any]]:
    """The overlay for picking card(s) out of a set -- Transform, Duplicate,
    or a multi-pick "Choose N cards" reward.

    Payload lives under `card_select`, not top-level `cards`/`options` (a real
    fixture showed that mistake looping forever on a `cancel_selection` that
    wasn't even valid, since `can_cancel` was false). `can_confirm` is the
    authoritative "enough selected" signal -- the prompt's count is not
    machine-readable and selections aren't reflected back to us.
    """
    cs = gs.raw.get("card_select") or {}

    if cs.get("preview_showing"):
        # Selecting first shows a preview of the result (e.g. what a Transform
        # rolled) before it's final -- accept it. Our pick already targeted our
        # worst card, so any random replacement is a fine trade.
        _card_select_visit["signature"] = None
        _card_select_visit["picked"] = set()
        return "confirm_selection", {}

    prompt_text = cs.get("prompt") or ""
    needed = _required_picks(prompt_text)
    signature_now = _card_select_signature(cs)
    already = len(_card_select_visit["picked"]) if _card_select_visit["signature"] == signature_now else 0
    if cs.get("can_confirm") and already >= needed:
        # Enough cards are selected -- lock it in. Deliberately do NOT clear
        # the picks here: if the confirm does not take, clearing makes us
        # re-select the same cards, which toggles them back off and guarantees
        # the next confirm fails too. The state is cleared when the screen
        # actually changes (signature differs), which is the real signal that
        # the selection went through.
        _card_select_visit["confirms"] += 1
        if _card_select_visit["confirms"] > MAX_CONFIRM_ATTEMPTS:
            # This prompt cannot be satisfied. Leave rather than spin -- the
            # freeze recovery would otherwise relaunch the game.
            _card_select_visit["signature"] = None
            _card_select_visit["picked"] = set()
            _card_select_visit["confirms"] = 0
            if cs.get("can_cancel", True):
                return "cancel_selection", {}
            return "proceed", {}
        return "confirm_selection", {}

    options = cs.get("cards") or []
    if not options:
        if cs.get("can_cancel", True):
            return "cancel_selection", {}
        return "confirm_selection", {}

    signature = _card_select_signature(cs)
    if _card_select_visit["signature"] != signature:
        _card_select_visit["signature"] = signature
        _card_select_visit["picked"] = set()
        _card_select_visit["confirms"] = 0

    picked: set = _card_select_visit["picked"]
    remaining = [c for c in options if c.get("index") not in picked]
    if not remaining:
        # Everything's been picked and it still won't confirm. Try the confirm
        # a bounded number of times, then leave -- re-picking from scratch is
        # what created the 32-attempt loop.
        _card_select_visit["confirms"] += 1
        if _card_select_visit["confirms"] > MAX_CONFIRM_ATTEMPTS:
            _card_select_visit["signature"] = None
            _card_select_visit["picked"] = set()
            _card_select_visit["confirms"] = 0
            if cs.get("can_cancel", True):
                return "cancel_selection", {}
            return "proceed", {}
        return "confirm_selection", {}

    screen_type = (cs.get("screen_type") or "").lower()
    prompt = (cs.get("prompt") or "").lower()
    # Prompts that GET RID of the chosen cards -- Transform, Gambling Chip's
    # start-of-combat "discard any number, then redraw", removal services.
    # Picking our *best* card on one of these hands away the good cards, which
    # is what the previous blanket "pick best" did outside Transform.
    discards_the_pick = screen_type == "transform" or any(
        k in prompt for k in ("discard", "replace", "exchange", "remove", "transform")
    )
    is_removal = "remove" in prompt or screen_type in ("remove", "purge")
    is_upgrade = "upgrade" in prompt or "enchant" in prompt or screen_type in ("upgrade", "smith")

    if is_upgrade or (is_removal and _removal_returns_upgraded(gs)):
        # A genuine upgrade screen, or a relic that hands the card back
        # upgraded -- either way pick by how much the card actually gains,
        # using the per-card upgrade data imported from the game.
        chosen = card_db.pick_upgrade_target(
            remaining, deck_memory.current_deck(gs), act=gs.act, floor=gs.floor
        ) or remaining[0]
    elif discards_the_pick:
        already_names = [
            c.get("name", "") for c in options if c.get("index") in picked
        ]
        chosen = _balanced_worst(
            remaining, deck_memory.current_deck(gs), already_names
        )
    elif gs.is_combat:
        # Mid-combat the card goes straight into our *hand* for this turn
        # (Skill Potion: "Choose 1 of 3 random Skill cards to add into your
        # Hand. It's free to play this turn."), so deck-building score is the
        # wrong metric entirely. A live run at 16/70 HP picked Knife Trap --
        # which the game itself annotated "(Plays 0 Shivs)" because the
        # exhaust pile was empty -- over Cloak and Dagger's 6 Block, then
        # discarded the useless card. Rank by what the card does *now*, and
        # never take one that can do nothing.
        from . import combat as combat_mod

        live = [c for c in remaining if not combat_mod._is_dud_this_turn(c, gs)] or remaining
        target = combat_mod._lowest_hp_enemy(gs.enemies)
        shiv_bonus = combat_mod._shiv_damage_bonus(gs)
        dexterity = combat_mod._player_dexterity(gs)

        def _now_value(card: dict[str, Any]) -> float:
            block = combat_mod._effective_block(card, dexterity)
            damage = (
                combat_mod._effective_damage(card, target, shiv_bonus)
                if target is not None
                else combat_mod._card_damage(card)
            )
            return block + damage

        # When every option is worthless by this metric the ranking is a tie
        # and `max` just takes whatever the game listed first. That is exactly
        # the Knowledge Demon "pick your penalty" screen, where the first item
        # was recurring self-damage. Fall back to least-harmful, judged
        # against whether this deck can actually block the bleed.
        if all(_now_value(c) <= 0 for c in live):
            chosen = min(live, key=lambda c: combat_mod._penalty_cost(c, gs))
        else:
            chosen = max(live, key=_now_value)
    else:
        # Add-to-deck / duplicate style: take the best card.
        chosen = _pick_best(remaining, deck_memory.current_deck(gs))

    picked.add(chosen.get("index"))
    return "select_card", {"index": chosen.get("index", 0)}


def decide_bundle_select(gs: GameState) -> tuple[str, dict[str, Any]]:
    """"Choose 1 of N packs of cards" (e.g. the Scroll Boxes relic).

    Payload lives under `bundle_select`, not top-level `bundles`/`options` --
    reading the wrong key made this fall through to `cancel_bundle_selection`,
    which isn't even valid here (`can_cancel` is false), so it errored in a
    loop. Same two-step preview flow as `card_select`: select, then confirm.

    Every card in the chosen bundle joins the deck, so bundles are scored by
    the summed synergy of their contents rather than any single standout card.
    """
    bs = gs.raw.get("bundle_select") or {}
    if bs.get("preview_showing"):
        return "confirm_bundle_selection", {}

    bundles = bs.get("bundles") or []
    if not bundles:
        if bs.get("can_cancel"):
            return "cancel_bundle_selection", {}
        return "confirm_bundle_selection", {}

    counts = card_db.deck_tag_counts(deck_memory.current_deck(gs))

    def _bundle_score(bundle: dict[str, Any]) -> float:
        return sum(card_db.score_card(c.get("name", ""), counts) for c in bundle.get("cards", []))

    best = max(bundles, key=_bundle_score)
    return "select_bundle", {"index": best.get("index", 0)}


# The divination minigame ("How to Divine"): an 11x11 Crystal Sphere grid.
# "Select Small Divination or Big Divination and reveal the tiles... Once an
# item is revealed you will receive it at the end of the session... But beware,
# revealing some items may be dangerous!"
#
# The old handler just sent `crystal_sphere_proceed`, which the game REJECTS
# while `can_proceed` is false -- you have to spend the divinations first. That
# left the bot erroring on the same screen until the freeze detector fired and
# relaunched the game, abandoning a floor-31 run. `crystal_sphere` was flagged
# "unverified, no live fixture ever captured" in KNOWLEDGE.md; there is one now.
#
# The action name for revealing a cell is still unknown, so candidates are
# tried in turn and the one that works is remembered for the rest of the
# session. Everything falls back to leaving the screen rather than looping.
_CRYSTAL_CLICK_ACTIONS = (
    "crystal_sphere_click",
    "crystal_sphere_select",
    "crystal_sphere_reveal",
    "crystal_sphere_select_cell",
)
_crystal_state: dict[str, Any] = {"action": None, "tried": 0}


def reset_crystal_sphere() -> None:
    _crystal_state["action"] = None
    _crystal_state["tried"] = 0


def decide_crystal_sphere(gs: GameState) -> tuple[str, dict[str, Any]]:
    sphere = (gs.raw or {}).get("crystal_sphere") or {}
    if not sphere:
        sphere = getattr(gs, "crystal_sphere", None) or {}

    # Divinations spent -- the game will now accept the proceed.
    if sphere.get("can_proceed"):
        reset_crystal_sphere()
        return "crystal_sphere_proceed", {}

    clickable = sphere.get("clickable_cells") or []
    if not clickable:
        # Nothing to click and we cannot proceed: leave rather than spin.
        reset_crystal_sphere()
        return "proceed", {}

    # Reveal the most central clickable tile. With no model of which tiles are
    # "dangerous", the centre is simply where the grid keeps most of its
    # options open; the choice is arbitrary but deliberate rather than random.
    width = sphere.get("grid_width") or 11
    height = sphere.get("grid_height") or 11
    cx, cy = (width - 1) / 2, (height - 1) / 2
    cell = min(
        clickable,
        key=lambda c: abs(c.get("x", 0) - cx) + abs(c.get("y", 0) - cy),
    )

    known = _crystal_state["action"]
    if known:
        return known, {"x": cell.get("x", 0), "y": cell.get("y", 0)}

    tried = _crystal_state["tried"]
    if tried >= len(_CRYSTAL_CLICK_ACTIONS):
        # None of the candidates worked. Leave the screen; the loop's escape
        # path handles it from here without relaunching the game.
        reset_crystal_sphere()
        return "proceed", {}
    _crystal_state["tried"] = tried + 1
    return _CRYSTAL_CLICK_ACTIONS[tried], {"x": cell.get("x", 0), "y": cell.get("y", 0)}


def note_crystal_sphere_result(action: str, ok: bool) -> None:
    """Remember whichever click action the game actually accepted."""
    if ok and action in _CRYSTAL_CLICK_ACTIONS:
        _crystal_state["action"] = action
