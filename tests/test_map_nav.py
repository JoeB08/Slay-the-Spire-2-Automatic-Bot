from bot.strategy import map_nav
from conftest import load_fixture


def test_returns_a_valid_option_index_on_real_map():
    map_data = load_fixture("map.json")["map"]
    idx = map_nav.choose_map_node_index(map_data, hp_pct=1.0)
    valid = {o["index"] for o in map_data["next_options"]}
    assert idx in valid


def test_single_option_is_taken_without_scoring():
    map_data = {"next_options": [{"index": 0, "col": 0, "row": 0, "type": "Monster"}], "nodes": []}
    assert map_nav.choose_map_node_index(map_data, hp_pct=1.0) == 0


def test_prefers_elite_path_when_healthy():
    nodes = [
        {"col": 0, "row": 0, "type": "Ancient", "children": [[0, 1], [1, 1]]},
        {"col": 0, "row": 1, "type": "Monster", "children": [[0, 2]]},
        {"col": 0, "row": 2, "type": "Monster", "children": []},
        {"col": 1, "row": 1, "type": "Elite", "children": [[1, 2]]},
        {"col": 1, "row": 2, "type": "Monster", "children": []},
    ]
    map_data = {
        "next_options": [
            {"index": 0, "col": 0, "row": 1, "type": "Monster"},
            {"index": 1, "col": 1, "row": 1, "type": "Elite"},
        ],
        "nodes": nodes,
    }
    assert map_nav.choose_map_node_index(map_data, hp_pct=1.0) == 1


def _two_path_map(other_type="Monster"):
    nodes = [
        {"col": 0, "row": 0, "type": "Ancient", "children": [[0, 1], [1, 1]]},
        {"col": 0, "row": 1, "type": other_type, "children": []},
        {"col": 1, "row": 1, "type": "Shop", "children": []},
    ]
    return {
        "next_options": [
            {"index": 0, "col": 0, "row": 1, "type": other_type},
            {"index": 1, "col": 1, "row": 1, "type": "Shop"},
        ],
        "nodes": nodes,
    }


def test_detours_to_a_shop_when_carrying_a_lot_of_gold():
    # Gold only becomes power at a shop, so a rich bot should seek one out.
    assert map_nav.choose_map_node_index(_two_path_map(), hp_pct=1.0, gold=400) == 1


def test_shop_is_not_forced_when_broke():
    # With no gold the shop is nearly worthless; an event path should win.
    assert map_nav.choose_map_node_index(_two_path_map("Unknown"), hp_pct=1.0, gold=0) == 0


def test_rest_still_wins_over_a_shop_when_badly_hurt():
    assert map_nav.choose_map_node_index(_two_path_map("RestSite"), hp_pct=0.2, gold=400) == 0


def test_refuses_an_elite_at_low_hp_even_when_the_path_beyond_looks_rich():
    """An elite scores negatively at low HP, but the lookahead adds whatever
    lies beyond it -- a treasure/shop-rich path could outweigh the penalty and
    walk the bot into an elite while nearly dead. Survival gates instead."""
    nodes = [
        {"col": 0, "row": 0, "type": "Ancient", "children": [[0, 1], [1, 1]]},
        # Safe but unrewarding.
        {"col": 0, "row": 1, "type": "Monster", "children": []},
        # Elite guarding a very attractive path.
        {"col": 1, "row": 1, "type": "Elite", "children": [[1, 2]]},
        {"col": 1, "row": 2, "type": "Treasure", "children": [[1, 3]]},
        {"col": 1, "row": 3, "type": "Shop", "children": [[1, 4]]},
        {"col": 1, "row": 4, "type": "RestSite", "children": []},
    ]
    map_data = {
        "next_options": [
            {"index": 0, "col": 0, "row": 1, "type": "Monster"},
            {"index": 1, "col": 1, "row": 1, "type": "Elite"},
        ],
        "nodes": nodes,
    }
    assert map_nav.choose_map_node_index(map_data, hp_pct=0.25, gold=400) == 0
    # Healthy, the same rich path is exactly what we should want.
    assert map_nav.choose_map_node_index(map_data, hp_pct=1.0, gold=400) == 1


def test_elite_still_taken_at_low_hp_when_it_is_the_only_option():
    map_data = {
        "next_options": [{"index": 0, "col": 1, "row": 1, "type": "Elite"}],
        "nodes": [{"col": 1, "row": 1, "type": "Elite", "children": []}],
    }
    assert map_nav.choose_map_node_index(map_data, hp_pct=0.15) == 0


def test_avoids_elite_path_when_low_hp():
    nodes = [
        {"col": 0, "row": 0, "type": "Ancient", "children": [[0, 1], [1, 1]]},
        {"col": 0, "row": 1, "type": "RestSite", "children": []},
        {"col": 1, "row": 1, "type": "Elite", "children": []},
    ]
    map_data = {
        "next_options": [
            {"index": 0, "col": 0, "row": 1, "type": "RestSite"},
            {"index": 1, "col": 1, "row": 1, "type": "Elite"},
        ],
        "nodes": nodes,
    }
    assert map_nav.choose_map_node_index(map_data, hp_pct=0.2) == 0
