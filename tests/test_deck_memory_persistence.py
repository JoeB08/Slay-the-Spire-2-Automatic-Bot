"""Deck memory must survive a bot restart.

The card piles are only visible during combat, so every deck-dependent
decision outside combat reads `deck_memory`. A restart left that cold until
the next fight -- a campfire hit right afterwards saw an empty deck, decided
nothing was worth upgrading, and healed instead (logged at act 1 floor 11,
55/77 HP).
"""
from bot import deck_memory
from bot.game_state import GameState
from bot.strategy import rest


def _combat_state(deck):
    return GameState({
        "state_type": "monster",
        "run": {"act": 1, "floor": 10},
        "battle": {"is_play_phase": True, "enemies": []},
        "player": {
            "hp": 55, "max_hp": 77, "gold": 0, "potions": [], "max_potion_slots": 3,
            "hand": [], "draw_pile": [{"name": n} for n in deck],
            "discard_pile": [], "exhaust_pile": [],
        },
    })


def _campfire_state():
    # Outside combat the piles are empty -- this is the real payload shape.
    return GameState({
        "state_type": "rest_site",
        "run": {"act": 1, "floor": 11},
        "rest_site": {"options": [
            {"index": 0, "id": "HEAL", "name": "Rest",
             "description": "Heal for 30% of your Max HP (23).", "is_enabled": True},
            {"index": 1, "id": "SMITH", "name": "Smith",
             "description": "Upgrade a card in your Deck.", "is_enabled": True},
        ]},
        "player": {
            "hp": 55, "max_hp": 77, "gold": 0, "potions": [], "max_potion_slots": 3,
            "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        },
    })


def test_deck_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(deck_memory, "_cache_path", lambda: tmp_path / "deck.json")

    deck = ["Strike", "Defend", "Footwork", "Adrenaline", "Backflip"]
    deck_memory.remember(_combat_state(deck))
    assert deck_memory.current_deck(_campfire_state()) == deck

    # Simulate the process dying and a new one starting.
    deck_memory.reset()
    assert deck_memory.current_deck(_campfire_state()) == []
    deck_memory.load()
    assert deck_memory.current_deck(_campfire_state()) == deck


def test_campfire_upgrades_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(deck_memory, "_cache_path", lambda: tmp_path / "deck.json")

    deck_memory.remember(_combat_state(["Strike", "Defend", "Footwork", "Adrenaline"]))
    deck_memory.reset()   # restart
    deck_memory.load()

    action, fields = rest.decide_rest(_campfire_state())
    assert action == "choose_rest_option"
    assert fields["index"] == 1, "should Smith at 71% HP, not heal"


def test_cold_memory_without_a_cache_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(deck_memory, "_cache_path", lambda: tmp_path / "missing.json")
    deck_memory.reset()
    deck_memory.load()  # must not raise
    assert deck_memory.current_deck(_campfire_state()) == []
