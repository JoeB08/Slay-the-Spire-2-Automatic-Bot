"""Silent card knowledge base: tags, base strength, and deck-synergy scoring.

Data source: bot/strategy/data/silent_cards.json, pulled from real STS2 pick-rate
stats (see file header). pick_rate is an empirical strength prior; synergy_score()
adjusts it up/down based on what's already in the deck and whether we're playing
solo (STS2 is co-op capable; some cards are useless without teammates).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).parent / "data" / "silent_cards.json"

with open(_DATA_PATH, "r", encoding="utf-8") as f:
    _DATA = json.load(f)

CARDS_BY_NAME: dict[str, dict[str, Any]] = {c["name"]: c for c in _DATA["cards"]}
STARTERS_BY_NAME: dict[str, dict[str, Any]] = {c["name"]: c for c in _DATA["starters"]}

# Per-card upgrade values, imported live from the game's own wiki endpoint by
# scripts/import_upgrade_data.py (it exposes both the base and upgraded
# variant of every discovered card). Cards upgrade very unevenly -- Tools of
# the Trade drops 1 -> 0 cost while a Strike gains 3 damage -- and without
# this the bot had no basis at all for choosing what to upgrade.
_UPGRADES_PATH = Path(__file__).parent / "data" / "silent_upgrades.json"
try:
    UPGRADE_DATA: dict[str, dict[str, Any]] = json.loads(
        _UPGRADES_PATH.read_text(encoding="utf-8")
    )["cards"]
except (OSError, KeyError, json.JSONDecodeError):
    UPGRADE_DATA = {}  # not imported yet -- upgrade_gain falls back to a default

# Used when a card has no imported data (undiscovered on this profile).
DEFAULT_UPGRADE_GAIN = 5.0


def upgrade_gain(name: str) -> float:
    """How much this card improves when upgraded (0 = nothing/unknown)."""
    entry = UPGRADE_DATA.get(name)
    if entry is None:
        return DEFAULT_UPGRADE_GAIN if card_info(name) else 0.0
    return float(entry.get("gain") or 0.0)


def upgrade_value(name: str, deck_card_names: list[str] | None = None) -> float:
    """Worth of upgrading this specific card, for choosing an upgrade target.

    Combines *how much it gains* with *how much we care about the card*: a
    huge upgrade on a card we never want to draw is worth less than a solid
    upgrade on the deck's workhorse. Junk can't be upgraded usefully at all.
    """
    info = card_info(name)
    if info is None:
        return 0.0  # curses/statuses and unknowns
    quality = _base_quality(name) or _DEFAULT_QUALITY
    # Clamp so quality modulates rather than dominates the gain.
    weight = max(0.5, min(1.5, quality / 50.0))
    return upgrade_gain(name) * weight

MULTIPLAYER_ONLY_PENALTY = -60  # solo play: these tags are dead weight
SYNERGY_BONUS = 8  # per matching tag already established in the deck
SLY_DISCARD_SYNERGY_BONUS = 15  # sly cards + discard engines reinforce each other hard
# Sly is strong *with a discard outlet* and merely fine without one. A flat
# bonus let it override an already-committed archetype, so it now scales with
# whether the deck can actually trigger it.
SLY_BONUS_WITH_ENGINE = 12
SLY_BONUS_UNCOMMITTED = 4

# --- archetype commitment ---------------------------------------------------
# The Silent wins by committing to one engine, not by collecting good cards.
# A 10-run sample showed picks scattered across poison, shivs and discard --
# base pick-rate dominated scoring, so synergy rarely changed the choice.
#
# Commitment ramps with progress: Act 1 is a *soft* lean, letting the early
# picks reveal which engine the run is offering; from early Act 2 it's a
# *hard* commit, where off-archetype cards are actively penalised.
ARCHETYPE_TAGS: dict[str, tuple[str, ...]] = {
    "poison": ("poison", "poison_payoff", "poison_synergy"),
    "shiv": ("shiv", "shiv_payoff"),
    "discard": ("discard_engine", "discard_synergy", "sly_enabler"),
}
SOFT_COMMIT_BONUS = 12  # Act 1: nudge toward the emerging engine
HARD_COMMIT_BONUS = 30  # Act 2+: strongly prefer the committed engine
HARD_COMMIT_PENALTY = -22  # Act 2+: actively steer away from off-archetype cards
# An archetype only counts as "emerging" once this many cards support it.
ARCHETYPE_MIN_CARDS = 2

# ...and it must be at least this many *actual cards*, not just enough weight.
# Noxious Fumes alone scores 2.55 and cleared the weight threshold by itself,
# committing the whole run to poison off a single pick. One strong Power is a
# direction, not a build: the second on-theme card is what makes it real.
ARCHETYPE_MIN_CARD_COUNT = 2

# How hard the act's boss leans on an undecided build. Enough to break a tie
# and to pull a drifting deck toward the archetype that actually beats it,
# not enough to override a deck that has already committed elsewhere.
BOSS_ARCHETYPE_BONUS = 1.5

# "Early" for upgrade purposes: act 1 up to the boss. Before that the deck is
# mostly starters and upgraded damage is what carries fights.
EARLY_UPGRADE_FLOOR = 17
EARLY_ATTACK_UPGRADE_BONUS = 15.0
EARLY_UTILITY_UPGRADE_PENALTY = 20.0
# Small thumb on the scale when engines are otherwise level. Shiv and poison
# close fights faster than a discard engine, which matters most at low
# ascension where survival is rarely the binding constraint -- a discard deck
# spends turns setting up that a shiv or poison deck spends killing.
ARCHETYPE_PRIOR: dict[str, float] = {"shiv": 0.6, "poison": 0.6, "discard": 0.0}


# A build is defined by its best card, not by how many cards carry a tag.
# Counting them made Noxious Fumes (pick rate 68, a Power, the poison engine)
# worth exactly as much as Deadly Poison (23) -- one point each -- so owning
# the single card that decides the build was not enough to commit to it.
ARCHETYPE_QUALITY_UNIT = 40.0   # roughly "a solidly playable card" = 1.0
ENGINE_TAG_WEIGHT = 1.5         # Powers and payoff cards *are* the engine
ENGINE_TAGS = frozenset({"power", "poison_payoff", "shiv_payoff", "discard_engine"})


def _archetype_weight(name: str) -> float:
    """How much one card argues for its archetype."""
    info = card_info(name)
    if not info:
        return 0.0
    quality = _base_quality(name) or _DEFAULT_QUALITY
    weight = quality / ARCHETYPE_QUALITY_UNIT
    if set(info.get("tags", [])) & ENGINE_TAGS:
        weight *= ENGINE_TAG_WEIGHT
    return weight


RELIC_ARCHETYPE_WEIGHT = 1.0    # "one solidly playable card" of evidence

# Relics whose text never names the archetype they belong to. Kunai and
# Shuriken read "every time you play 3 Attacks in a single turn..." -- that is
# a *shiv* relic in practice, because playing three attacks in one turn is
# what a shiv deck does and almost nothing else can. Pure description matching
# scores them 0, which is how a run ended up holding shiv relics while the
# archetype sat undecided. Keyed on base name; the text rules below still
# catch anything not listed here.
RELIC_ARCHETYPE_NAMES: dict[str, str] = {
    "Kunai": "shiv",
    "Shuriken": "shiv",
    "Ornamental Fan": "shiv",
    "Wrist Blade": "shiv",
    "Ninja Scroll": "shiv",
    "Tough Bandages": "discard",
    "Tingsha": "discard",
    "Snecko Skull": "poison",
}

# ...and the mechanic behind them, so an unlisted equivalent still registers.
_MANY_ATTACKS_RE = re.compile(
    r"(\d+) attacks? in a (?:single )?turn", re.IGNORECASE
)
_ZERO_COST_ATTACK_RE = re.compile(
    r"0[- ]cost attack", re.IGNORECASE
)


def _relic_archetype_scores(relics: list[dict[str, Any]] | None) -> dict[str, float]:
    """How much the relics we hold argue for each archetype.

    Relics were invisible to archetype detection entirely. A live run held
    Snecko Skull ("Whenever you apply Poison, apply an additional 1 Poison")
    while committing to shiv, with poison scored at 0.60 -- the single
    strongest poison signal available was contributing nothing.

    Matched on the relic's description rather than a hardcoded name list: the
    STS2 relic set is not fully known here, and the text is what actually says
    what the relic does. Weighted so a relic alone does not commit the build
    (1.0 + a 0.6 prior is under the threshold of 2) but a relic plus a single
    on-theme card does.
    """
    scores: dict[str, float] = {}
    for relic in relics or []:
        name = base_name(relic.get("name") or "")
        desc = relic.get("description") or ""
        text = (desc + " " + name).lower()
        hits: set[str] = set()

        known = RELIC_ARCHETYPE_NAMES.get(name)
        if known:
            hits.add(known)
        # "Play 3 Attacks in a single turn" and "0-cost attacks" are shiv
        # mechanics: nothing else reliably plays that many attacks a turn.
        if _MANY_ATTACKS_RE.search(desc) or _ZERO_COST_ATTACK_RE.search(desc):
            hits.add("shiv")
        for archetype in ARCHETYPE_TAGS:
            if archetype in text:
                hits.add(archetype)

        for archetype in hits:
            if archetype in ARCHETYPE_TAGS:
                scores[archetype] = scores.get(archetype, 0.0) + RELIC_ARCHETYPE_WEIGHT
    return scores


def dominant_archetype(
    deck_card_names: list[str], relics: list[dict[str, Any]] | None = None
) -> str | None:
    """Which engine this deck is actually building, or None if undecided.

    Starter cards are excluded deliberately. `Survivor` ("Gain 8 Block.
    Discard 1 card.") is tagged as a discard engine and ships in the starting
    deck, so discard began every run one card ahead of shiv and poison -- a
    single discard pick then locked the build in. Across a 10-run sample the
    bot committed to discard in 5 of the 7 runs that committed at all, while
    skipping early shiv and poison cards. The build should be decided by the
    cards we *chose*, not by what we were dealt.
    """
    chosen = [n for n in deck_card_names if base_name(n) not in STARTERS_BY_NAME]
    relic_scores = _relic_archetype_scores(relics)
    scores: dict[str, float] = {}
    counts: dict[str, int] = {}
    for name, tags in ARCHETYPE_TAGS.items():
        total = ARCHETYPE_PRIOR.get(name, 0.0) + relic_scores.get(name, 0.0)
        matching = 0
        for card in chosen:
            info = card_info(card)
            if info and set(info.get("tags", [])) & set(tags):
                total += _archetype_weight(card)
                matching += 1
        scores[name] = total
        counts[name] = matching
    # The act's boss can settle an undecided build. Lagavulin Matriarch sits
    # behind 12 Block a turn, which Poison ignores and nothing else gets
    # through, so a run heading for it should commit to poison rather than
    # drift into shiv.
    from . import boss_intel

    wanted = boss_intel.preferred_archetype()
    if wanted and wanted in scores:
        scores[wanted] += BOSS_ARCHETYPE_BONUS

    best = max(scores, key=lambda k: scores[k], default=None)
    if best is None or scores[best] < ARCHETYPE_MIN_CARDS:
        return None
    # Relics can say which way a split deck is leaning, but they cannot stand
    # in for the cards -- the build is not committed until the deck has two of
    # them.
    if counts.get(best, 0) < ARCHETYPE_MIN_CARD_COUNT:
        return None
    # A tie means nothing has emerged yet.
    if list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def _archetype_adjustment(card_name: str, archetype: str | None, act: int) -> float:
    """Bonus/penalty for how well a card fits the run's chosen engine."""
    if not archetype:
        return 0.0
    info = card_info(card_name)
    if not info:
        return 0.0
    tags = set(info.get("tags", []))
    on_archetype = bool(tags & set(ARCHETYPE_TAGS[archetype]))

    if act <= 1:  # soft: encourage, never punish -- the engine isn't settled
        return SOFT_COMMIT_BONUS if on_archetype else 0.0
    if on_archetype:
        return HARD_COMMIT_BONUS
    # Cards that are strong regardless of engine (draw, energy, block) keep
    # their value -- only genuinely off-plan cards get steered away from.
    if tags & {"energy", "draw", "block", "power", "dexterity"}:
        return 0.0
    return HARD_COMMIT_PENALTY


# Upgraded cards arrive from the API with the upgrade baked into the *name*
# ("Strike+", "Afterimage+"), and every lookup here is keyed on the base name.
# Unstripped, an upgraded card matched nothing: Afterimage+ scored 0.0 instead
# of 89.0, so as a run upgraded its deck the bot progressively went blind to
# its own best cards -- rating them as junk to remove, ignoring them when
# reading the deck's archetype, and (because every candidate tied at 0) picking
# the first card offered whenever it had to choose an upgrade target.
_UPGRADE_SUFFIX_RE = re.compile(r"\s*\++\d*$")


def base_name(name: str) -> str:
    """Card name with any upgrade marker stripped ("Strike+" -> "Strike")."""
    return _UPGRADE_SUFFIX_RE.sub("", name or "").strip()


def is_upgraded(card: dict[str, Any]) -> bool:
    """Whether this card payload is already upgraded."""
    if card.get("is_upgraded"):
        return True
    name = card.get("name") or card.get("card_name") or ""
    return name != base_name(name)


def card_info(name: str) -> dict[str, Any] | None:
    name = base_name(name)
    return CARDS_BY_NAME.get(name) or STARTERS_BY_NAME.get(name)


# What a pristine starter does. Anything printing more than this has been
# improved -- upgraded, or enchanted by a relic like Gnarled Hammer ("Upon
# pickup, Enchant up to 3 Attacks with Sharp 3").
#
# `removal_priority` keyed on the *name* alone, so a Strike reading "Deal 9
# damage" scored 503 -- prime junk -- exactly like a fresh one. The bot would
# enchant three Strikes with Sharp 3 and then delete them at the next shop,
# throwing the relic away. Removal screens do carry the real text, which is
# precisely where the distinction matters.
_STARTER_BASELINE = {"Strike": 6, "Defend": 5}
_STARTER_NUMBER_RE = re.compile(r"(?:Deal|Gain)\s+(\d+)", re.IGNORECASE)
# Below this much improvement it is still basically a starter.
STARTER_IMPROVEMENT_MARGIN = 2


def _starter_improvement(card: dict[str, Any]) -> int:
    """How far a starter's printed effect exceeds a pristine copy, or 0.

    Returns 0 when the card carries no text -- most callers pass only a name,
    and guessing there would be worse than the status quo.
    """
    name = base_name(card.get("name") or card.get("card_name") or "")
    baseline = _STARTER_BASELINE.get(name)
    if baseline is None:
        return 0
    text = card.get("description") or card.get("text") or ""
    m = _STARTER_NUMBER_RE.search(text)
    if not m:
        return 0
    return max(0, int(m.group(1)) - baseline)


def removal_priority(card: dict[str, Any], deck_card_names: list[str] | None = None) -> float:
    """Higher = better to remove/transform out of the deck.

    Deliberately NOT the inverse of `score_card`: that ranks by pick rate, so
    a starter Strike (no pick rate -> default 30) looked *better* than a weak
    real card like Slice (5), and the bot would remove Slice while keeping the
    Strike. Thinning starters is the single biggest consistency gain for a
    Silent deck, so they're prime targets -- behind only genuine junk.
    """
    name = card.get("name") or card.get("card_name") or ""
    card_type = (card.get("type") or "").lower()
    # Some screens report the class under `rarity` rather than `type`, and a
    # curse that slips through here is a curse the bot will never cut.
    rarity = (card.get("rarity") or card.get("card_rarity") or "").lower()

    if card_type == "curse" or rarity == "curse":
        return 1000.0
    if card_type == "status" or rarity == "status":
        return 900.0
    # Quest cards are reward markers, not deck cards -- "Spoils Map" reads
    # "Unplayable. Marks a site of 600 extra Gold in the next Act." Removing
    # one throws the reward away. They are unplayable, so they are still fine
    # to *discard* in combat; they must simply never be cut from the deck.
    if card_type == "quest" or rarity == "quest":
        return -1000.0

    # Grand Finale ("can only be played if there are no cards in your draw
    # pile") makes the deck itself the obstacle: every other card is something
    # standing between us and firing it, so all of them become worth cutting.
    # Rest sites and events both gate removal on "is there junk to remove",
    # which went false as soon as the starters were gone -- so a Grand Finale
    # deck stopped thinning at exactly the point thinning mattered most.
    #
    # Everything clears the junk threshold (100), but weaker cards still rank
    # above stronger ones so the worst go first. Grand Finale itself is never
    # a target.
    if deck_card_names and holds_grand_finale(deck_card_names):
        if base_name(name) == GRAND_FINALE:
            return -1000.0
        quality = _base_quality(name) or _DEFAULT_QUALITY
        return 500.0 + max(0.0, 100.0 - quality)

    # An improved starter is not junk any more. Enchanting a Strike to 9
    # damage and then removing it as a starter wastes the enchant outright.
    improvement = _starter_improvement(card)
    if improvement > STARTER_IMPROVEMENT_MARGIN:
        return 40.0 - improvement

    if base_name(name) in STARTERS_BY_NAME:
        # Keep at least a couple of starters early on: removing every Strike
        # before the deck has replacements leaves nothing to actually play.
        # Count by base name: an upgraded Strike is still a Strike, and
        # counting "Strike+" separately hid how many copies the deck held.
        base_of = base_name(name)
        copies = sum(1 for n in (deck_card_names or []) if base_name(n) == base_of)
        # Strike/Defend are the classic cuts; Neutralize/Survivor pull weight
        # (Weak application and a discard outlet) so they rank lower. Matched
        # on the base name so Strike+ does not slip into the protected band
        # and get kept while a real card is cut instead.
        base = 500.0 if base_of in ("Strike", "Defend") else 200.0
        # Between two otherwise identical starters, cut the un-upgraded one
        # and keep the upgrade.
        return base + copies - (0.5 if is_upgraded(card) else 0.0)

    info = card_info(name)
    pick_rate = (info or {}).get("pick_rate")
    if pick_rate is None:
        return 100.0  # unknown card -- middling
    return 100.0 - pick_rate  # weaker real cards are better cuts


def pick_upgrade_target(
    offered: list[dict[str, Any]],
    deck_card_names: list[str] | None = None,
    act: int = 1,
    floor: int = 0,
) -> dict[str, Any] | None:
    """Best card to feed to a "remove it, get it back upgraded" effect.

    This is the *opposite* of `removal_priority`. That effect isn't thinning
    the deck, it's an upgrade in disguise, so the usual prime cuts are the
    worst picks: upgrading a starter Strike/Defend is nearly worthless, and
    curses/status can't be upgraded usefully at all.

    Targets the card that gains the most from being upgraded, weighted by how
    much we actually value it (`upgrade_value`).

    This previously picked the *median-scoring* card as a stand-in for "middle
    of the pack", because no upgrade data existed at all. Real per-card
    upgrade values are now imported from the game itself, so the pick is made
    on actual benefit -- Tools of the Trade going 1 -> 0 cost is worth far
    more than a card that gains 2 damage.
    """
    if not offered:
        return None

    def _eligible(card: dict[str, Any]) -> bool:
        card_type = (card.get("type") or "").lower()
        if card_type in ("curse", "status"):
            return False
        if is_upgraded(card):
            return False  # upgrading it again gains nothing
        name = card.get("name") or card.get("card_name") or ""
        return base_name(name) not in STARTERS_BY_NAME

    candidates = [c for c in offered if _eligible(c)] or list(offered)

    def _ranked(card: dict[str, Any]) -> float:
        name = card.get("name") or card.get("card_name") or ""
        value = upgrade_value(name, deck_card_names)
        # Early on, an upgraded attack carries the run: the deck is still
        # mostly starters and every fight is a damage race. A cycler like
        # Prepared ("Draw 1 card. Discard 1 card.") upgrades into a slightly
        # better cycler, which does nothing for the floors that are actually
        # killing us. Later, when the deck has an engine, utility upgrades
        # start paying.
        if act <= 1 and floor <= EARLY_UPGRADE_FLOOR:
            if (card.get("type") or "").lower() == "attack":
                value += EARLY_ATTACK_UPGRADE_BONUS
            elif _is_pure_utility(base_name(name)):
                value -= EARLY_UTILITY_UPGRADE_PENALTY
        return value

    return max(candidates, key=_ranked)


def is_sly(card: dict[str, Any]) -> bool:
    """True if a live card payload has the Sly keyword.

    Sly means "if discarded from your Hand before the end of your turn, play
    it for free" -- so *paying energy* to play a Sly card throws away its whole
    point. The bot should discard these (triggering them free) and spend its
    energy elsewhere. Live payloads mark it both in `keywords` and as a "Sly."
    prefix on the description; check both, then fall back to our static card
    data for payloads that carry only a name.
    """
    for kw in card.get("keywords") or []:
        if (kw.get("name") or "").strip().lower() == "sly":
            return True
    description = card.get("description") or card.get("card_description") or ""
    if re.match(r"\s*sly\b", description, re.IGNORECASE):
        return True
    info = card_info(card.get("name") or card.get("card_name") or "")
    return bool(info and info.get("sly"))


def deck_tag_counts(deck_card_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in deck_card_names:
        info = card_info(name)
        if not info:
            continue
        for tag in info.get("tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    return counts


# Tags that make a card a build-around payoff worth chasing once its enabler is present.
_PAYOFF_ENABLER_PAIRS = [
    ("sly_enabler", "sly"),
    ("discard_engine", "sly"),  # sly cards want discard outlets and vice versa
    ("poison_payoff", "poison"),
    ("shiv_payoff", "shiv"),
    ("weak_synergy", "weak"),
    ("draw_synergy", "draw"),
    ("scales_with_plays", "draw"),  # more draw -> more plays per turn
]


# A card we have no data for is *unknown*, not *bad*. Scoring it 0.0 conflated
# the two: it made every unfamiliar card the first thing to discard, and made
# it unpickable as a reward, exactly the way upgraded names once were. That
# matters more after a new Act unlocks, which brings cards the data file has
# never seen. Curses and Status are excluded from rewards by type elsewhere,
# so a middling default here is safe.
UNKNOWN_CARD_QUALITY = 30.0


# --- intrinsic value ------------------------------------------------------
#
# Pick rate says how often people take a card. It says nothing about what the
# card *does*, and `score_card` used nothing else -- so a starter Strike (no
# pick rate, default 30) outscored Pounce+ (20 damage, 25) and Precise Cut+
# (16 damage for 0 energy, 19). The bot skipped all three at floor 2 with a
# bare starter deck, and kept skipping rewards at floors 30 and 35.
#
# So blend the community signal with the card's own numbers, per energy. Pick
# rate still carries the things text cannot express -- consistency, synergy,
# how a card actually plays -- which is why this is a blend and not a
# replacement.
INTRINSIC_WEIGHT = 0.5      # half stats, half pick rate
INTRINSIC_SCALE = 3.0       # puts value-per-energy on the same 0-100 footing
# A point of Block is worth a little less than a point of damage.
#
# Scaling this with run progress was tried and reverted: `_blend_quality`
# returns max(pick_rate, blended), so for every card it would have affected
# the pick rate already dominated and the reweighting produced identical
# numbers early and late. The behaviour it was meant to deliver -- take Dagger
# Spray (22) and refuse Speedster (18) on floor 2 -- comes from the scaled
# absolute floor instead.
BLOCK_VALUE = 0.8
POISON_VALUE = 1.5          # poison ignores Block and keeps ticking
# `card_info` strips the "+" to look a card up, so the static entry holds the
# *unupgraded* text -- Pounce+ was being valued at base Pounce's damage and
# scored below a starter Strike. Upgrades add roughly a third to a card's
# numbers, so approximate rather than mis-state them. (The live payload has
# the true text; this path only ever sees a name.)
UPGRADE_INTRINSIC_BONUS = 1.3
# Printed numbers lie when the card carries a condition. Grand Finale reads
# "Deal 60 damage" and scored 180 -- but only with an empty draw pile, which
# is why its pick rate is 7. Cards whose damage varies ("for each ...") are
# discounted rather than zeroed: the number is real, just rarely the maximum.
_GATED_RE = re.compile(r"can only be played", re.IGNORECASE)
_VARIABLE_RE = re.compile(r"for each", re.IGNORECASE)
VARIABLE_DISCOUNT = 0.5


def _blend_quality(name: str, pick_rate: float, act: int | None = None, floor: int = 0) -> float:
    """Community rating tempered by what the card actually does.

    Only applied when the card *has* parseable numbers. A utility card --
    Prepared, Footwork, Adrenaline -- has no damage, Block or poison in its
    text, so a straight blend would score its stats as zero and halve it. That
    regression made the bot skip Prepared from a starter deck. Text silence
    means "this card's value isn't expressible in numbers", not "worthless",
    so those keep their pick rate untouched.
    """
    intrinsic = intrinsic_value(name, act, floor)
    if intrinsic <= 0:
        return pick_rate
    blended = (1.0 - INTRINSIC_WEIGHT) * pick_rate + INTRINSIC_WEIGHT * intrinsic
    # Only ever *raise* a card. Blending down punished cards whose worth is
    # real but unparseable -- Hand Trick (pick rate 31, only 7 Block in its
    # text) fell below the bar and stopped being taken.
    return max(pick_rate, blended)


def intrinsic_value(name: str, act: int | None = None, floor: int = 0) -> float:
    """What the card is worth from its own text, per energy spent.

    `act`/`floor` are accepted for callers that have run context; Block's
    weight is currently flat (see BLOCK_VALUE).
    """
    info = card_info(name)
    if not info:
        return 0.0
    from . import combat as combat_mod  # deferred: combat imports this module

    text = info.get("text") or ""
    if _GATED_RE.search(text):
        # Its damage isn't available in an ordinary deck; whatever makes the
        # card playable is handled separately (see GRAND_FINALE_MAX_DECK).
        return 0.0
    card = {"description": text, "name": name}
    damage = combat_mod._card_damage(card)
    damage += combat_mod._card_shiv_count(card) * combat_mod.SHIV_BASE_DAMAGE
    block = combat_mod._card_block(card)
    poison = combat_mod._card_poison(card)

    raw = damage + block * BLOCK_VALUE + poison * POISON_VALUE
    try:
        cost = float(info.get("cost"))
    except (TypeError, ValueError):
        cost = 1.0  # X-cost and unparsable: treat as one energy
    value = (raw / max(cost, 1.0)) * INTRINSIC_SCALE
    if name != base_name(name):
        value *= UPGRADE_INTRINSIC_BONUS
    if _VARIABLE_RE.search(text):
        value *= VARIABLE_DISCOUNT
    return value


def score_card(name: str, deck_tag_counts_: dict[str, int]) -> float:
    """Higher is better. Used for card-reward picks and shop buys."""
    info = card_info(name)
    if not info:
        return UNKNOWN_CARD_QUALITY

    pick_rate = float(info.get("pick_rate") or 30)  # unranked/basic get a modest default
    score = _blend_quality(name, pick_rate)
    tags = set(info.get("tags", []))
    if info.get("sly"):
        tags.add("sly")  # `sly` is its own boolean field, not a list tag -- fold it in here

    if "multiplayer_only" in tags:
        score += MULTIPLAYER_ONLY_PENALTY

    for tag in tags:
        if tag in deck_tag_counts_:
            score += SYNERGY_BONUS * min(deck_tag_counts_[tag], 3)

    for enabler_tag, payoff_tag in _PAYOFF_ENABLER_PAIRS:
        if enabler_tag in tags and deck_tag_counts_.get(payoff_tag, 0) > 0:
            score += SLY_DISCARD_SYNERGY_BONUS
        if payoff_tag in tags and deck_tag_counts_.get(enabler_tag, 0) > 0:
            score += SLY_DISCARD_SYNERGY_BONUS

    # Sly is the Silent's headline mechanic, so it gets a nudge -- but only
    # while the deck is still uncommitted or actually discard-based. A live
    # run building a shiv deck took a Sly card over Blade Dance because this
    # flat bonus outweighed the archetype lean, which is the opposite of
    # committing to an engine.
    if info.get("sly") and deck_tag_counts_.get("discard_engine", 0) > 0:
        score += SLY_BONUS_WITH_ENGINE
    elif info.get("sly"):
        score += SLY_BONUS_UNCOMMITTED

    return score


GRAND_FINALE = "Grand Finale"


def holds_grand_finale(deck_card_names: list[str]) -> bool:
    """Is Grand Finale in the deck, upgraded or not?

    `GRAND_FINALE in deck_card_names` is an exact string match, so an upgraded
    copy ("Grand Finale+") slipped straight past it: a live run held Grand
    Finale+ in a 20-card deck and carried on taking every card reward, which
    is the exact opposite of what the card wants.
    """
    return any(base_name(n) == GRAND_FINALE for n in deck_card_names)

# Grand Finale can only be played with an empty draw pile, which is a real
# constraint, not a bonus: it has the *lowest* pick rate of any Silent card
# (7%) and negative act/run winrate deltas in the source stats. It's only
# worth taking into a deck already small enough to plausibly empty -- every
# card, junk included, sits in the draw pile and works against it.
GRAND_FINALE_MAX_DECK = 15

# Card intake is front-loaded: take freely early to actually build a deck,
# then tighten as the run goes on and a mediocre card mostly dilutes it.
FLOORS_PER_ACT = 17
TOTAL_ACTS = 3
# Deck curve: the Silent has 3 energy a turn, so a deck of 2-3 cost cards
# strands most of it. Only penalise once the deck is already top-heavy.
EXPENSIVE_COST = 2
CURVE_EXPENSIVE_SHARE = 0.30  # comfortable ceiling for cost-2+ cards
CURVE_PENALTY_WEIGHT = 40.0
CURVE_MIN_DECK = 8  # too few cards to judge a curve
EARLY_BAR_FACTOR = 0.55  # start of Act 1 -- take most things, the deck needs bodies
LATE_BAR_FACTOR = 1.45  # late Act 3 -- only genuine upgrades to a built deck


# Starters are the cards we spend removals to delete, so they must count as
# weak when measuring deck quality -- not as average.
STARTER_QUALITY = 15.0
_DEFAULT_QUALITY = 30.0
# Some cards are simply too weak to build around at any point in a run --
# Speedster (18) was taken in a live run purely because the deck it was
# joining was also weak, which is exactly when a bad card does most harm. The
# deck-relative bar alone can't express that, so this is an absolute floor.
# Raised from 22: a 10-run sample took 58 cards and skipped 9, ending with
# ~22-card decks where the weakest additions never earned their slot.
# An absolute floor stops the bot taking junk just because its deck is bad.
# Held flat, though, it applied the same standard on floor 2 with twelve
# starters as on floor 45 with a tuned deck -- and since it sits *above* the
# blended quality of many ordinary commons, it vetoed them outright. A logged
# floor-2 offer of Dagger Spray (22), Deadly Poison (23) and Slice (11) was
# skipped entirely from a starter deck. The deck-relative bar already scales
# with run progress; this now scales with it, so the floor is permissive when
# the deck is empty and strict once it isn't.
MIN_ABSOLUTE_QUALITY = 28.0        # the late-run standard
# Calibrated against two live observations, not picked round: it must admit
# Dagger Spray (22) and Deadly Poison (23), skipped from a floor-2 starter
# deck, while still refusing Speedster (18) and Slice (11.5) -- Speedster was
# explicitly called out as too weak to build around.
MIN_ABSOLUTE_QUALITY_EARLY = 20.0  # floor 1, bare starter deck


def _absolute_floor(act: int, floor: int) -> float:
    """The absolute quality floor, scaled by how far the run has come."""
    progress = _run_progress(act, floor)
    return MIN_ABSOLUTE_QUALITY_EARLY + (MIN_ABSOLUTE_QUALITY - MIN_ABSOLUTE_QUALITY_EARLY) * progress


def _base_quality(name: str) -> float | None:
    """A card's intrinsic strength, with no deck-synergy credit.

    The bar must not use `score_card`: synergy bonuses are awarded per copy of
    a shared tag, so five starter Strikes each collect the `damage` bonus and
    score ~54 -- pushing the bar above genuinely good cards and making the bot
    skip everything from a starting deck. Intrinsic quality is the honest
    yardstick; synergy is credited to the *candidate*, where it belongs.
    """
    info = card_info(name)
    if not info:
        return None  # unknown (usually a status/curse) -- not a yardstick
    if base_name(name) in STARTERS_BY_NAME:
        return STARTER_QUALITY
    # Same blend `score_card` uses, minus synergy. Pick rate alone is why the
    # reward gate skipped Pounce+ (20 damage) and Precise Cut+ (16 for 0
    # energy): their community ratings sit below MIN_ABSOLUTE_QUALITY even
    # though the cards are plainly worth taking. Both sides of the comparison
    # move together, since the deck's bar is a median of this same measure.
    pick_rate = info.get("pick_rate")
    return float(pick_rate) if pick_rate else _DEFAULT_QUALITY


def _card_cost(name: str) -> Optional[int]:
    info = card_info(name)
    if info is None:
        return None
    try:
        return int(info.get("cost"))
    except (TypeError, ValueError):
        return EXPENSIVE_COST  # "X" cost -- treat as expensive for curve purposes


def _curve_penalty(name: str, deck_card_names: list[str]) -> float:
    """Discourage a deck that can't afford its own cards.

    The Silent has 3 energy a turn, so a hand of 2- and 3-cost cards strands
    most of it. Nothing in the scoring considered cost at all, so a deck could
    drift top-heavy unchecked.

    Deliberately only bites once the deck is *already* expensive (past
    `CURVE_EXPENSIVE_SHARE`): measured decks sat at 0-22% cost-2+, which is
    healthy, and a blanket penalty would wrongly refuse strong expensive
    cards in a cheap deck.
    """
    cost = _card_cost(name)
    if cost is None or cost < EXPENSIVE_COST:
        return 0.0

    playable = [n for n in deck_card_names if card_info(n)]
    if len(playable) < CURVE_MIN_DECK:
        return 0.0
    expensive = sum(1 for n in playable if (_card_cost(n) or 0) >= EXPENSIVE_COST)
    share = expensive / len(playable)
    if share <= CURVE_EXPENSIVE_SHARE:
        return 0.0
    # Scale with how far past the comfortable share we already are.
    return -CURVE_PENALTY_WEIGHT * (share - CURVE_EXPENSIVE_SHARE) / max(1e-6, 1 - CURVE_EXPENSIVE_SHARE)


def _run_progress(act: int, floor: int) -> float:
    """How far through the run we are, 0.0 at the start and ~1.0 by late Act 3.

    Acts are roughly 17 floors, so this reads as "acts completed".
    """
    return min(1.0, max(0.0, ((act - 1) + floor / FLOORS_PER_ACT) / TOTAL_ACTS))


def _progress_bar_factor(act: int, floor: int) -> float:
    """Multiplier on the take/skip bar, by how far through the run we are.

    Deck building is front-loaded: early on almost anything beats a starter
    and there are many fights left to get value from a pick, so the bar is
    deliberately low. Later a mediocre card mostly dilutes the deck and has
    few fights left to pay off, so the bar rises and the bot takes less.
    """
    return EARLY_BAR_FACTOR + (LATE_BAR_FACTOR - EARLY_BAR_FACTOR) * _run_progress(act, floor)


def _deck_quality_bar(
    deck_card_names: list[str],
    archetype: str | None = None,
    act: int = 1,
    floor: int = 0,
) -> float:
    """The score a new card must beat to be worth adding.

    The median intrinsic quality of the real cards we already hold, scaled by
    run progress. Junk (curses/statuses) is excluded: it isn't something a new
    card replaces, and counting it would *lower* the bar exactly when the deck
    is most clogged. As the deck improves the median rises, so the bot grows
    pickier on its own without any arbitrary deck-size cap.
    """
    # Same measure the candidate is judged by. Leaving the bar on raw pick
    # rate while the candidate gets the stats blend put a thumb on the scale
    # toward taking everything -- exactly the asymmetry the note in `_passes`
    # warns about, just with intrinsic value standing in for synergy.
    scores = [
        _blend_quality(n, q)
        for n, q in ((n, _base_quality(n)) for n in deck_card_names)
        if q is not None
    ]
    if not scores:
        return 0.0
    scores.sort()
    median = scores[len(scores) // 2]
    return median * _progress_bar_factor(act, floor)


def score_card_for_deck(
    name: str, deck_card_names: list[str], act: int = 1,
    relics: list[dict[str, Any]] | None = None,
) -> float:
    """Score including archetype commitment for the current act."""
    counts = deck_tag_counts(deck_card_names)
    archetype = dominant_archetype(deck_card_names, relics)
    return score_card(name, counts) + _archetype_adjustment(name, archetype, act)


# Diminishing returns on copies. Nothing stopped the bot taking a fourth
# Prepared ("Draw 1 card. Discard 1 card.") -- four cyclers mostly cycle into
# each other, and each one is a card that is not damage or Block.
#
# Cards that scale with copies (damage, Block, poison appliers) get the mild
# penalty; cards whose value is card-flow get the steep one, because the
# second copy is worth much less than the first and the fourth is noise.
# Multiplicative, not flat: Prepared scores 101 on pick rate alone, so any
# fixed penalty leaves it winning. What actually changes with copies is how
# much the *next* one is worth, so decay the score instead.
#
# Some cards genuinely want multiples -- more Deadly Poison is more poison,
# more Blade Dance is more Shivs. A cycler is the opposite: the second
# Prepared is a good deal worse than the first, and the fourth mostly draws
# into the others.
DUPLICATE_DECAY = 0.80          # damage / Block / poison: still want copies
UTILITY_DUPLICATE_DECAY = 0.45  # card-flow only: falls off fast
# Copies we take before any decay applies. Two of a good card is usually right.
FREE_COPIES = 1


def _is_pure_utility(name: str) -> bool:
    """No damage, no Block, no poison -- its whole job is moving cards."""
    from . import combat as combat_mod

    info = card_info(name) or {}
    text = info.get("description") or info.get("text") or ""
    card = {"name": name, "description": text}
    if not text:
        return False
    # Energy generation is not "just moving cards" -- Adrenaline ("Gain 2
    # Energy. Draw 2 cards.") is a genuine engine piece and upgrading it is
    # worth more than any attack. The penalty is aimed at cyclers like
    # Prepared ("Draw 1 card. Discard 1 card."), whose upgrade buys a slightly
    # better cycler.
    if combat_mod._GAINS_ENERGY_RE.search(text):
        return False
    return not (
        combat_mod._card_damage(card)
        or combat_mod._card_block(card)
        or combat_mod._card_poison(card)
        or combat_mod._card_shiv_count(card)
    )


def _duplicate_multiplier(name: str, deck_card_names: list[str] | None) -> float:
    """How much of a card's score survives, given copies already held.

    1.0 for the first two; then decayed per extra copy, steeply for cards
    whose only job is moving other cards.
    """
    if not deck_card_names:
        return 1.0
    base = base_name(name)
    if base in STARTERS_BY_NAME:
        return 1.0  # starters are handled by removal, not by intake
    copies = sum(1 for n in deck_card_names if base_name(n) == base)
    excess = max(0, copies - FREE_COPIES)
    if not excess:
        return 1.0
    decay = UTILITY_DUPLICATE_DECAY if _is_pure_utility(base) else DUPLICATE_DECAY
    return decay ** excess


def _boss_block_bonus(name: str) -> float:
    """Extra credit for Block when the act's boss demands surviving it."""
    from . import boss_intel
    from . import combat as combat_mod

    bonus = boss_intel.block_bonus()
    if not bonus:
        return 0.0
    info = card_info(name) or {}
    text = info.get("description") or info.get("text") or ""
    if not text:
        return 0.0
    return bonus if combat_mod._card_block({"description": text}) > 0 else 0.0


def best_card_reward_index(
    cards: list[dict[str, Any]], deck_card_names: list[str], act: int = 1, floor: int = 0,
    relics: list[dict[str, Any]] | None = None,
) -> int | None:
    """Pick a card from a card_reward screen, or None to skip.

    Skipping is driven purely by quality *relative to the deck we already
    have*: a card must beat the median of our existing real cards. A 10-run
    sample took 43 cards and skipped 2, ending with 24-card decks that still
    held every starter -- a fixed low bar means the deck only ever grows.

    Exception: once we own Grand Finale ("can only be played if there are no
    cards in your Draw Pile"), the deck itself is the win condition. Every
    extra card makes emptying the draw pile harder, so we stop adding
    entirely and let removals do the rest. Taking it in the first place is
    gated on deck size -- see `GRAND_FINALE_MAX_DECK`.
    """
    if not cards:
        return None

    if holds_grand_finale(deck_card_names):
        return None  # building toward an empty draw pile -- take nothing

    archetype = dominant_archetype(deck_card_names, relics)
    counts = deck_tag_counts(deck_card_names)

    def _score(card: dict[str, Any]) -> float:
        name = card.get("name", "")
        raw = score_card(name, counts) + _archetype_adjustment(name, archetype, act)
        # Some bosses are survived rather than out-damaged. Lagavulin
        # Matriarch hits for 23 behind 12 Block a turn, so the deck heading
        # for it wants poison *and* enough Block to live through the swings
        # while the poison ticks.
        raw += _boss_block_bonus(name)
        return raw * _duplicate_multiplier(name, deck_card_names)

    # Grand Finale only pays off in a deck we can actually empty. Taken into a
    # normal-sized deck it's a dead card, which is why it has the worst pick
    # rate in the pool -- so it has to earn its slot on deck size, not on
    # being a build-around. Below the threshold it's the pick; above it, it
    # falls through and gets judged on its (poor) intrinsic quality like
    # anything else.
    gf = next((c for c in cards if base_name(c.get("name", "")) == GRAND_FINALE), None)
    if gf is not None and len(deck_card_names) <= GRAND_FINALE_MAX_DECK:
        return gf["index"]

    # Gate first, *then* pick. Doing it the other way round meant synergy
    # chose a candidate and the quality gate judged that same card: an
    # intrinsically weak card with stacking tags (Flick-Flack, pick rate 19)
    # could out-score a solid one (Backstab, 43), fail the absolute floor, and
    # take the whole reward down with it -- the bot skipped offers containing
    # a card it should obviously have taken.
    bar = _deck_quality_bar(deck_card_names, archetype, act, floor)

    def _passes(card: dict[str, Any]) -> bool:
        name = card.get("name", "")
        base = _base_quality(name)
        if base is None:
            return False  # unknown card -- not worth adding blind
        # Judge the *candidate* on pick rate blended with what it actually
        # does. Deliberately not applied to `_base_quality` itself: that same
        # function is the yardstick for archetype detection and for the deck's
        # median bar, both tuned against the community rating, and moving all
        # three at once makes any result unattributable. The defect is here --
        # Pounce+ (20 damage) and Precise Cut+ (16 for 0 energy) were refused
        # because their community ratings sit below MIN_ABSOLUTE_QUALITY.
        quality = _blend_quality(name, base, act, floor)
        # Absolute floor: a genuinely bad card is bad even in a bad deck, and
        # the deck-relative bar is lowest exactly when the deck is weakest.
        if quality < _absolute_floor(act, floor) and base_name(name) not in STARTERS_BY_NAME:
            return False
        # Intrinsic quality on both sides of the comparison: crediting the
        # candidate with synergy while the bar has none biases toward taking.
        # Copies decay here too, or a fourth Prepared still clears the bar on
        # pick rate alone and ends up the only eligible card in the offer.
        return (
            (quality + _archetype_adjustment(name, archetype, act))
            * _duplicate_multiplier(name, deck_card_names)
            + _curve_penalty(name, deck_card_names)
        ) > bar

    eligible = [c for c in cards if _passes(c)]
    if not eligible:
        return None  # nothing here improves the deck -- stay lean

    # Among the cards worth taking, synergy decides which one.
    return max(eligible, key=_score)["index"]
