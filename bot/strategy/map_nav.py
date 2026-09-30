"""Map routing.

STS2MCP hands us the *entire* floor graph up front (`map.nodes`, each with its
`children` as [col,row] pairs), not just the immediate choices -- so instead of
a purely greedy next-step pick, we score a short lookahead toward the boss and
pick the immediate option with the best downstream potential.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import deck_memory, route_memory
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


# What a node costs on arrival, in HP: the 75th percentile of recorded fights
# (1,265 in act 1, 175 in act 2; later acts reuse act 2). An unknown room is
# sometimes a fight. Set 3 lost runs 4, 6 and 7 to routes that each looked fine
# one node at a time -- the lookahead scored every node with today's HP, so
# two elites and no rest site looked as safe as one monster, and a shop two
# floors on outweighed a rest site at 4 HP.
NODE_HP_COST = {
    1: {"Monster": 9, "Elite": 38, "Unknown": 5},
    2: {"Monster": 16, "Elite": 44, "Unknown": 8},
}
REST_HEAL_FRACTION = 0.3
REST_HEALS_BELOW = 0.7  # a rest site on the route is assumed to heal below this
# p75 is not the worst case (act 1 monsters reach 18 at p90), so a route that
# leaves less than this share of max HP is dangerous (DANGER_VALUE); one that
# reaches 0 is death. The route beyond a dangerous node still counts -- a rest
# site right after it is exactly what saves the run.
DEATH_HP_FRACTION = 0.1
DEATH_VALUE = -100.0
DANGER_VALUE = -50.0


def _node_hp_cost(node_type: str, act: int) -> int:
    return (NODE_HP_COST.get(act) or NODE_HP_COST[2]).get(node_type, 0)


def _hp_after(node_type: str, hp: float, max_hp: int, act: int) -> float:
    if node_type == "RestSite":
        if hp < REST_HEALS_BELOW * max_hp:
            return min(float(max_hp), hp + REST_HEAL_FRACTION * max_hp)
        return hp
    return hp - _node_hp_cost(node_type, act)


def _children(
    nodes_by_pos: dict[tuple[int, int], dict[str, Any]], node: dict[str, Any]
) -> list[dict[str, Any]]:
    return [nodes_by_pos[tuple(c)] for c in node.get("children") or [] if tuple(c) in nodes_by_pos]


def _best_path_value(
    nodes_by_pos: dict[tuple[int, int], dict[str, Any]],
    node: dict[str, Any],
    hp: float,
    max_hp: int,
    depth: int,
    gold: int = 0,
    junk_cards: int = 0,
    real_cards: int = EARLY_CARD_TARGET,
    act: int = 1,
) -> float:
    """Value of the best route through `node`, with HP carried along it.

    Each node is scored at the HP we arrive with, then costs what a fight of
    its kind costs. A route that runs us out of HP is worth DEATH_VALUE --
    no amount of treasure beyond it counts.
    """
    node_type = node.get("type", "")
    value = _node_value(node_type, hp / max_hp, gold, junk_cards, real_cards)
    hp_after = _hp_after(node_type, hp, max_hp, act)
    if hp_after <= 0:
        return DEATH_VALUE
    # Below the safety line a route is dangerous, not over: a rest site right
    # after the fight still saves it. Scoring it DEATH_VALUE made every option
    # look equally dead at low HP -- set 4, run 1, floor 7: 12/70 between two
    # Monsters, one leading to a rest site, and the tie went to the first.
    if hp_after < DEATH_HP_FRACTION * max_hp:
        value += DANGER_VALUE
    children = _children(nodes_by_pos, node)
    if not children or depth <= 0:
        return value
    best_child = max(
        _best_path_value(nodes_by_pos, c, hp_after, max_hp, depth - 1,
                         gold, junk_cards, real_cards, act)
        for c in children
    )
    # Every continuation dies: that stays negative. Otherwise a mildly
    # negative continuation (an elite at low HP) is floored at 0, as before.
    if best_child > DEATH_VALUE / 2:
        best_child = max(0.0, best_child)
    return value + DECAY * best_child


def hp_at_next_rest(
    start: dict[str, Any], hp: float, max_hp: int, act: int,
    nodes_by_pos: dict[tuple[int, int], dict[str, Any]],
) -> Optional[float]:
    """HP on reaching the next rest site or boss from `start`, by the best route.

    `start`'s own effect is not applied -- this is what we would arrive with
    if we left it as we are. None when there is nowhere to go.
    """
    def reach(node: dict[str, Any], hp_now: float, depth: int) -> float:
        node_type = node.get("type", "")
        if node_type in ("RestSite", "Boss"):
            return hp_now
        hp_after = hp_now - _node_hp_cost(node_type, act)
        children = _children(nodes_by_pos, node)
        if not children or depth <= 0:
            return hp_after
        return max(reach(c, hp_after, depth - 1) for c in children)

    children = _children(nodes_by_pos, start)
    if not children:
        return None
    return max(reach(c, hp, 20) for c in children)


def choose_map_node_index(
    map_data: dict[str, Any], hp_pct: float, gold: int = 0, junk_cards: int = 0,
    real_cards: int = EARLY_CARD_TARGET, hp: Optional[float] = None,
    max_hp: Optional[int] = None, act: int = 1,
) -> int:
    options = map_data.get("next_options") or []
    if not options:
        return 0
    if len(options) == 1:
        return options[0]["index"]
    if max_hp is None:
        max_hp = 70
    if hp is None:
        hp = hp_pct * max_hp

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
        value = _best_path_value(nodes_by_pos, node, hp, max_hp, LOOKAHEAD_DEPTH,
                                 gold, junk_cards, real_cards, act)
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
    idx = choose_map_node_index(gs.map, gs.hp_pct, gs.gold, junk, real_cards,
                                hp=gs.hp, max_hp=gs.max_hp, act=gs.act)
    # The rest site cannot see the map; it reads what lies ahead from here.
    route_memory.remember(gs.map, idx, gs.floor)
    return "choose_map_node", {"index": idx}
