"""The map, remembered past the map screen.

Only the map screen carries the node graph; a rest site does not. Deciding
there whether to heal needs to know what comes next -- the boss, or a long
run of fights with no rest -- so the map screen leaves the graph and the node
it chose here.

In-process only: after a restart the memory is empty and the callers fall
back to what the current screen shows.
"""
from __future__ import annotations

from typing import Any, Optional

_state: dict[str, Any] = {"nodes": {}, "position": None, "floor": None}


def reset() -> None:
    _state["nodes"] = {}
    _state["position"] = None
    _state["floor"] = None


def remember(map_data: dict[str, Any], index: int, floor: int) -> None:
    """Record the graph and the node the map screen just chose.

    `floor` is the floor the map screen was shown on; the chosen node is
    reached on the next one, which is how a stale memory is recognised.
    """
    nodes = map_data.get("nodes") or []
    chosen = next((o for o in map_data.get("next_options") or [] if o.get("index") == index), None)
    if not nodes or chosen is None:
        return
    _state["nodes"] = {(n.get("col"), n.get("row")): n for n in nodes}
    _state["position"] = (chosen.get("col"), chosen.get("row"))
    _state["floor"] = floor + 1


def current_node(floor: int) -> Optional[dict[str, Any]]:
    """The node the last map choice led to, or None if unknown or stale."""
    if _state["position"] is None or _state["floor"] != floor:
        return None
    return _state["nodes"].get(_state["position"])


def nodes_by_pos() -> dict[tuple[int, int], dict[str, Any]]:
    return _state["nodes"]


def children(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = _state["nodes"]
    return [nodes[tuple(c)] for c in node.get("children") or [] if tuple(c) in nodes]
