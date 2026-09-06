"""Map routing.

STS2MCP hands us the *entire* floor graph up front (`map.nodes`, each with its
`children` as [col,row] pairs), not just the immediate choices -- so instead of
a purely greedy next-step pick, we score a short lookahead toward the boss and
pick the immediate option with the best downstream potential.
"""
from __future__ import annotations

from typing import Any

from .. import deck_memory
from ..game_state import GameState
from . import cards as card_db

# Cards a removal would delete -- what makes a shop worth a detour.
JUNK_CARD_NAMES = {"Strike", "Defend"}

LOOKAHEAD_DEPTH = 6
DECAY = 0.85  # nearer nodes matter a bit more than distant ones

# Gold only converts into power at a shop -- unspent gold at the end of a run
# is worth exactly nothing. Once we're carrying enough for a real purchase
# (a card removal plus something else), a shop becomes worth detouring for,
# scaling up as the pile grows so a rich bot actively seeks one out.
SHOP_GOLD_POOR = 75  # can't afford anything meaningful -- a shop is near-worthless
SHOP_GOLD_INTERESTING = 150
SHOP_GOLD_RICH = 300
SHOP_VALUE_POOR = 0.5
SHOP_VALUE_BASE = 2.5
SHOP_VALUE_INTERESTING = 7.0
SHOP_VALUE_RICH = 12.0
# Removal is the strongest lever on deck quality, and shops are where it
# reliably lives -- so a junk-heavy deck detours for one.
JUNK_FOR_REMOVAL_DETOUR = 8   # starters still clogging the deck
TYPICAL_REMOVAL_PRICE = 75    # observed price in every logged shop
REMOVAL_DETOUR_BONUS = 6.0

# Below this, staying alive outranks any amount of shopping -- gold is no use
# to a corpse, and the next fight is what kills you.
CRITICAL_HP_FRACTION = 0.35
REST_VALUE_CRITICAL = 15.0
# Below this, an elite is refused outright rather than merely down-weighted --
# see the gate in choose_map_node_index.
ELITE_DANGER_HP_FRACTION = 0.5


def _shop_value(gold: int, junk_cards: int = 0) -> float:
    """How much a shop is worth detouring for.

    Gold alone understates it. Across a 10-run sample, deck quality was by far
    the strongest predictor of how deep a run got (real non-starter cards vs
    floor reached: +0.77; starters remaining: -0.71), and shops are the most
    reliable source of card removal. So a deck still clogged with starters
    should seek shops out harder than the gold pile alone suggests -- but only
    when there's enough gold to actually buy the removal.
    """
    if gold >= SHOP_GOLD_RICH:
        value = SHOP_VALUE_RICH
    elif gold >= SHOP_GOLD_INTERESTING:
        value = SHOP_VALUE_INTERESTING
    elif gold < SHOP_GOLD_POOR:
        value = SHOP_VALUE_POOR
    else:
        value = SHOP_VALUE_BASE

    if junk_cards >= JUNK_FOR_REMOVAL_DETOUR and gold >= TYPICAL_REMOVAL_PRICE:
        value += REMOVAL_DETOUR_BONUS
    return value


# Normal fights are the deck's supply line: each one is a card reward. Scoring
# them 0.5 against an Elite's 6.0 made the bot beeline elites with a starter
# deck -- and 45% of all recorded deaths are at floors 7-9, the first elite,
# with a median deck of 14. Early on, a few ordinary fights are worth more than
# the elite they pay for.
EARLY_CARD_TARGET = 5          # real cards to bank before elites look attractive
MONSTER_VALUE_EARLY = 7.0      # while the deck is still being built
MONSTER_VALUE_BASE = 0.5       # once it is
ELITE_THIN_DECK_VALUE = 1.0    # an elite fought on a starter deck is a coin flip


def _monster_value(real_cards: int) -> float:
    """Worth of a normal fight, tapering as the deck fills."""
    if real_cards >= EARLY_CARD_TARGET:
        return MONSTER_VALUE_BASE
    shortfall = (EARLY_CARD_TARGET - real_cards) / EARLY_CARD_TARGET
    return MONSTER_VALUE_BASE + (MONSTER_VALUE_EARLY - MONSTER_VALUE_BASE) * shortfall


def _node_value(node_type: str, hp_pct: float, gold: int = 0, junk_cards: int = 0,
                real_cards: int = EARLY_CARD_TARGET) -> float:
    if node_type == "Elite":
        if hp_pct <= 0.45:
            return -3.0
        # HP is not the only readiness test: a deck with nothing in it loses
        # to an elite at full health just as reliably.
        return 6.0 if real_cards >= EARLY_CARD_TARGET else ELITE_THIN_DECK_VALUE
    if node_type == "RestSite":
        if hp_pct < CRITICAL_HP_FRACTION:
            return REST_VALUE_CRITICAL
        return 4.0 if hp_pct < 0.75 else 1.0
    if node_type == "Shop":
        return _shop_value(gold, junk_cards)
    if node_type == "Treasure":
        return 2.5
    if node_type == "Unknown":  # "?" events
        return 1.5
    if node_type == "Monster":
        return _monster_value(real_cards)
    return 0.0  # Boss, Ancient, unrecognized


def _best_path_value(
    nodes_by_pos: dict[tuple[int, int], dict[str, Any]],
    node: dict[str, Any],
    hp_pct: float,
    depth: int,
    memo: dict[tuple[int, int], float],
    gold: int = 0,
    junk_cards: int = 0,
    real_cards: int = EARLY_CARD_TARGET,
) -> float:
    key = (node.get("col"), node.get("row"))
    if key in memo:
        return memo[key]
    value = _node_value(node.get("type", ""), hp_pct, gold, junk_cards, real_cards)
    children = node.get("children") or []
    if not children or depth <= 0:
        memo[key] = value
        return value
    best_child = 0.0
    found_child = False
    for c in children:
        child_key = tuple(c)
        child_node = nodes_by_pos.get(child_key)
        if child_node is None:
            continue
        found_child = True
        best_child = max(
            best_child,
            _best_path_value(nodes_by_pos, child_node, hp_pct, depth - 1, memo,
                             gold, junk_cards, real_cards),
        )
    total = value + (DECAY * best_child if found_child else 0.0)
    memo[key] = total
    return total


def choose_map_node_index(
    map_data: dict[str, Any], hp_pct: float, gold: int = 0, junk_cards: int = 0,
    real_cards: int = EARLY_CARD_TARGET,
) -> int:
    options = map_data.get("next_options") or []
    if not options:
        return 0
    if len(options) == 1:
        return options[0]["index"]

    nodes = map_data.get("nodes") or []
    nodes_by_pos = {(n.get("col"), n.get("row")): n for n in nodes}

    # Survival is a gate, not a trade-off. An Elite scores negatively at low
    # HP, but the lookahead then *adds* whatever lies beyond it, so a path
    # through an elite could still win on total value -- which is how the bot
    # walked into an elite while nearly dead. Downstream value is worth
    # nothing if that fight ends the run, so at low HP elites are removed from
    # consideration outright whenever any other option exists.
    if hp_pct < ELITE_DANGER_HP_FRACTION:
        safer = [o for o in options if (o.get("type") or "") != "Elite"]
        if safer:
            options = safer
            if len(options) == 1:
                return options[0]["index"]

    scored = []
    for opt in options:
        node = nodes_by_pos.get((opt.get("col"), opt.get("row")), opt)
        memo: dict[tuple[int, int], float] = {}
        value = _best_path_value(nodes_by_pos, node, hp_pct, LOOKAHEAD_DEPTH, memo,
                                 gold, junk_cards, real_cards)
        scored.append((value, opt["index"]))

    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def decide_map(gs: GameState) -> tuple[str, dict[str, Any]]:
    deck = deck_memory.current_deck(gs)
    junk = sum(1 for n in deck if n in JUNK_CARD_NAMES)
    # Cards we actually picked, which is what decides whether the deck is
    # ready for an elite -- starters don't count, and neither do the tokens a
    # fight generated.
    real_cards = sum(
        1 for n in deck
        if card_db.base_name(n) not in card_db.STARTERS_BY_NAME and n not in JUNK_CARD_NAMES
    )
    idx = choose_map_node_index(gs.map, gs.hp_pct, gs.gold, junk, real_cards)
    return "choose_map_node", {"index": idx}
