"""HP along the route: the map projects it, the rest site heals for what is ahead."""
from bot import route_memory
from bot.game_state import GameState
from bot.strategy import map_nav, rest

REST = {"index": 0, "type": None, "name": "Rest", "text": "Heal for 30% of your Max HP (21)."}
SMITH = {"index": 1, "type": None, "name": "Smith", "text": "Upgrade a card in your Deck."}


def _rest_state(hp, floor, act=1, max_hp=70):
    return GameState({
        "state_type": "rest_site",
        "run": {"act": act, "floor": floor},
        "rest_site": {"options": [dict(REST), dict(SMITH)]},
        "player": {"hp": hp, "max_hp": max_hp, "gold": 100, "potions": [], "max_potion_slots": 3,
                   "hand": [], "draw_pile": [{"name": n} for n in ("Strike", "Deadly Poison")],
                   "discard_pile": [], "exhaust_pile": []},
    })


def _chosen(gs, fields):
    option = next(o for o in gs.rest_site["options"] if o["index"] == fields["index"])
    return rest._option_kind(option)


def _line(types, start_row):
    """A single-file route: one node per row, each leading to the next."""
    nodes = []
    for i, t in enumerate(types):
        row = start_row + i
        nodes.append({"col": 0, "row": row, "type": t,
                      "children": [[0, row + 1]] if i < len(types) - 1 else []})
    return nodes


def _remember_line(types, floor):
    """The map screen on `floor` chose the first node of `types`."""
    nodes = _line(types, floor)
    route_memory.remember({"nodes": nodes, "next_options": [
        {"index": 0, "col": 0, "row": floor, "type": types[0]}]}, 0, floor)


# --- the rest site before the boss ------------------------------------------

def test_heals_before_the_boss():
    # Set 3, run 8: Smith at 46/70 before Vantom, lost on round 14 with
    # Vantom on 23 -- 21 HP more very likely wins it.
    _remember_line(["RestSite", "Boss"], floor=15)
    _, fields = rest.decide_rest(_rest_state(hp=46, floor=16))
    assert _chosen(_rest_state(hp=46, floor=16), fields) == "rest"


def test_heals_at_the_act_1_boss_rest_even_without_the_map():
    gs = _rest_state(hp=46, floor=16)
    _, fields = rest.decide_rest(gs)
    assert _chosen(gs, fields) == "rest"


def test_still_upgrades_before_the_boss_when_nearly_full():
    _remember_line(["RestSite", "Boss"], floor=15)
    gs = _rest_state(hp=64, floor=16)  # the heal returns 6
    _, fields = rest.decide_rest(gs)
    assert _chosen(gs, fields) == "upgrade"


def test_still_upgrades_mid_act_when_healthy():
    gs = _rest_state(hp=46, floor=10)
    _, fields = rest.decide_rest(gs)
    assert _chosen(gs, fields) == "upgrade"


# --- the rest site before a long run of fights -------------------------------

def test_heals_before_a_long_run_of_fights_with_no_rest():
    # Set 3, run 6: Smith at 37/70 on floor 8, then ?, Treasure and five
    # Monsters with no rest site: 37 -> 16 -> 12 -> 12 -> 3 -> dead.
    _remember_line(["RestSite", "Unknown", "Treasure"] + ["Monster"] * 5 + ["RestSite"], floor=7)
    gs = _rest_state(hp=37, floor=8)
    _, fields = rest.decide_rest(gs)
    assert _chosen(gs, fields) == "rest"


def test_upgrades_when_the_stretch_ahead_is_short():
    _remember_line(["RestSite", "Monster", "RestSite"], floor=7)
    gs = _rest_state(hp=37, floor=8)
    _, fields = rest.decide_rest(gs)
    assert _chosen(gs, fields) == "upgrade"


def test_a_memory_from_another_floor_is_ignored():
    _remember_line(["RestSite"] + ["Monster"] * 6, floor=3)
    gs = _rest_state(hp=37, floor=8)
    _, fields = rest.decide_rest(gs)
    assert _chosen(gs, fields) == "upgrade"


# --- the map projects HP along each route ------------------------------------

def _fork(left, right):
    """Two routes from row 1: `left` and `right` are lists of node types."""
    nodes = [{"col": 0, "row": 0, "type": "Ancient", "children": [[0, 1], [1, 1]]}]
    for col, types in ((0, left), (1, right)):
        for i, t in enumerate(types):
            row = 1 + i
            nodes.append({"col": col, "row": row, "type": t,
                          "children": [[col, row + 1]] if i < len(types) - 1 else []})
    return {"nodes": nodes, "next_options": [
        {"index": 0, "col": 0, "row": 1, "type": left[0]},
        {"index": 1, "col": 1, "row": 1, "type": right[0]}]}


def test_rests_at_4_hp_even_with_a_shop_beyond_the_fight():
    # Set 3, run 4: at 4/70 and again at 10/70 the Monster won over the
    # RestSite -- ~500 gold made the shop beyond it worth 18. Dead on floor 8.
    m = _fork(["Monster", "Shop"], ["RestSite", "Monster"])
    for hp in (4, 10):
        assert map_nav.choose_map_node_index(m, hp / 70, gold=500, junk_cards=10,
                                             hp=hp, max_hp=70) == 1


def test_avoids_a_route_with_two_elites_and_no_rest():
    # Set 3, run 7: Elite at 43/70 onto a route with a second, forced elite
    # and no rest site: 43 -> 18 -> 11 -> 6 -> dead.
    m = _fork(["Elite", "Treasure", "Monster", "Elite", "Monster"],
              ["Monster", "Unknown", "RestSite", "Monster", "Monster"])
    assert map_nav.choose_map_node_index(m, 43 / 70, real_cards=5, hp=43, max_hp=70) == 1


def test_a_healthy_bot_still_takes_the_elite():
    m = _fork(["Elite", "Treasure", "RestSite"], ["Monster", "Monster", "Monster"])
    assert map_nav.choose_map_node_index(m, 1.0, real_cards=5, hp=70, max_hp=70) == 0


def test_heads_for_the_rest_site_when_every_option_is_a_fight_at_low_hp():
    # Set 4, run 1, floor 7: 12/70 between two Monsters, one leading to a
    # rest site. Both scored as death, and the tie went to the first option.
    # Checked in both orders so a tie cannot pass.
    to_rest = ["Monster", "RestSite", "Monster"]
    no_rest = ["Monster", "Monster", "Monster"]
    assert map_nav.choose_map_node_index(_fork(to_rest, no_rest), 12 / 70, hp=12, max_hp=70) == 0
    assert map_nav.choose_map_node_index(_fork(no_rest, to_rest), 12 / 70, hp=12, max_hp=70) == 1


def test_the_map_screen_leaves_its_choice_for_the_rest_site():
    nodes = _line(["RestSite", "Boss"], 15)
    route_memory.remember({"nodes": nodes, "next_options": [
        {"index": 0, "col": 0, "row": 15, "type": "RestSite"}]}, 0, 15)
    node = route_memory.current_node(16)
    assert node["type"] == "RestSite"
    assert [k["type"] for k in route_memory.children(node)] == ["Boss"]
    assert route_memory.current_node(17) is None
