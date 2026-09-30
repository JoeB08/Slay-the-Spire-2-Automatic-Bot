import requests

from bot.loop import STALE_ACTION_LIMIT, BotLoop


class _FakeClient:
    """Minimal ApiClient double: always returns a valid, ready, non-combat
    state, but post_action raises a network-level exception -- this is what
    crashed the bot for real (an uncaught ReadTimeout right after a recovery
    relaunch) before post_action's except clause covered RequestException."""

    def __init__(self):
        self.post_calls = 0

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        return {
            "state_type": "event",
            "ready_for_command": True,
            "event": {"options": [{"index": 0, "is_proceed": True}]},
            "run": {"act": 1, "floor": 1},
            "player": {"hp": 70, "max_hp": 70, "gold": 0},
        }

    def post_action(self, action, **fields):
        self.post_calls += 1
        raise requests.exceptions.ReadTimeout("simulated network timeout")


def test_network_timeout_during_post_action_does_not_crash_the_loop(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # skip real backoff delays in the test

    client = _FakeClient()
    loop = BotLoop(client=client, max_actions=3)
    loop.run()  # would raise requests.exceptions.ReadTimeout before the fix
    loop.close()

    assert client.post_calls == 3


class _StaleCombatClient:
    """A combat state that never actually changes no matter what's played --
    reproduces the live Calculated Gamble bug: play_card keeps reporting "ok"
    but hand/energy/block/enemy HP stay byte-identical forever."""

    def __init__(self):
        self.actions_posted = []

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        return {
            "state_type": "monster",
            "ready_for_command": True,
            "battle": {
                "is_play_phase": True,
                "enemies": [
                    {"entity_id": "E_0", "name": "Dummy", "hp": 999, "max_hp": 999, "block": 0, "status": [], "intents": []}
                ],
            },
            "run": {"act": 1, "floor": 1},
            "player": {
                "hp": 70,
                "max_hp": 70,
                "gold": 0,
                "energy": 3,
                "max_energy": 3,
                "block": 0,
                "status": [],
                "hand": [
                    {
                        "id": "STRIKE_SILENT",
                        "name": "Strike",
                        "cost": "1",
                        "description": "Deal 6 damage.",
                        "index": 0,
                        "target_type": "AnyEnemy",
                        "can_play": True,
                    }
                ],
                "draw_pile": [],
                "discard_pile": [],
                "exhaust_pile": [],
            },
        }

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        return {"status": "ok"}


class _LaggingClient:
    """Serves the same combat state for several polls after an action before
    finally reflecting it -- the Grand Finale case, where a fight-ending card
    resolves but the mod keeps returning the pre-play combat state for a beat.
    Acting again during that window plays cards into a dead combat."""

    def __init__(self, lag_polls=4):
        self.lag_polls = lag_polls
        self.actions_posted = []
        self.polls_since_action = 0

    def wait_until_ready(self, *a, **kw):
        return {}

    def _combat_state(self, enemy_hp):
        return {
            "state_type": "monster",
            "ready_for_command": True,
            "battle": {
                "is_play_phase": True,
                "enemies": [
                    {"entity_id": "E_0", "name": "Dummy", "hp": enemy_hp, "max_hp": 99, "block": 0, "status": [], "intents": []}
                ],
            },
            "run": {"act": 1, "floor": 1},
            "player": {
                "hp": 70, "max_hp": 70, "gold": 0, "energy": 3, "max_energy": 3, "block": 0, "status": [],
                "hand": [
                    {
                        "id": "STRIKE_SILENT", "name": "Strike", "cost": "1", "description": "Deal 6 damage.",
                        "index": 0, "target_type": "AnyEnemy", "can_play": True,
                    }
                ],
                "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }

    def get_state(self):
        if not self.actions_posted:
            return self._combat_state(99)
        self.polls_since_action += 1
        # Stale for `lag_polls` polls, then the damage finally shows up.
        return self._combat_state(99 if self.polls_since_action <= self.lag_polls else 50)

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        return {"status": "ok"}


def test_waits_for_lagging_state_instead_of_spamming_actions(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _LaggingClient(lag_polls=4)
    loop = BotLoop(client=client, max_actions=2)
    loop.run()
    loop.close()

    # Exactly 2 actions for 2 decisions -- the lag window is absorbed by
    # polling, not by firing extra cards into a state that hasn't caught up.
    assert len(client.actions_posted) == 2


class _EnemyTurnClient:
    """Combat sitting in the enemy's phase (is_play_phase False) for a long
    stretch, then handing the turn back. The bot should just poll throughout
    and never be goaded into acting."""

    def __init__(self, enemy_turn_polls=10):
        self.enemy_turn_polls = enemy_turn_polls
        self.polls = 0
        self.actions_posted = []

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        self.polls += 1
        return {
            "state_type": "monster",
            "ready_for_command": True,
            "battle": {
                "is_play_phase": self.polls > self.enemy_turn_polls,
                "enemies": [
                    {"entity_id": "E_0", "name": "Dummy", "hp": 99, "max_hp": 99, "block": 0, "status": [],
                     "intents": [{"type": "Attack", "label": "23", "title": "", "description": ""}]}
                ],
            },
            "run": {"act": 1, "floor": 1},
            "player": {
                "hp": 40, "max_hp": 70, "gold": 0, "energy": 3, "max_energy": 3, "block": 0, "status": [],
                "hand": [
                    {"id": "DEFEND_SILENT", "name": "Defend", "cost": "1", "description": "Gain 5 Block.",
                     "index": 0, "target_type": "Self", "can_play": True},
                ],
                "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        return {"status": "ok"}


def test_polling_through_the_enemy_turn_never_forces_an_end_turn(tmp_path, monkeypatch):
    # Live bug: "state" no-op polls were counted as repeated decisions, so the
    # stale/cycle detectors force-ended the turn mid-fight -- throwing away 3
    # energy and playable Defends against a 23-damage hit.
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _EnemyTurnClient(enemy_turn_polls=10)
    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    assert ("end_turn", {}) not in client.actions_posted
    # Once the play phase returns it should block against the big hit.
    assert client.actions_posted == [("play_card", {"card_index": 0})]


def test_hand_churning_cards_get_a_longer_settle_delay():
    from bot.game_state import GameState
    from bot.loop import HAND_CHURN_DELAY, POST_ACTION_DELAY, _settle_delay_for

    gs = GameState(
        {
            "state_type": "monster",
            "player": {
                "hp": 70, "max_hp": 70, "gold": 0, "potions": [
                    {"slot": 0, "name": "Swift Potion", "description": "Draw 3 cards."},
                    {"slot": 1, "name": "Fire Potion", "description": "Deal 20 damage."},
                ],
                "hand": [
                    {"index": 0, "name": "Strike", "description": "Deal 6 damage."},
                    {"index": 1, "name": "Calculated Gamble",
                     "description": "Discard your Hand, then draw that many cards. Exhaust."},
                ],
            },
        }
    )

    # Plain attack resolves quickly; hand churn needs longer (both live
    # freezes came from hand-manipulation cards).
    assert _settle_delay_for(gs, "play_card", {"card_index": 0}) == POST_ACTION_DELAY
    assert _settle_delay_for(gs, "play_card", {"card_index": 1}) == HAND_CHURN_DELAY
    # Potions count too -- Swift Potion draws.
    assert _settle_delay_for(gs, "use_potion", {"slot": 0}) == HAND_CHURN_DELAY
    assert _settle_delay_for(gs, "use_potion", {"slot": 1}) == POST_ACTION_DELAY
    # Non-card actions are unaffected.
    assert _settle_delay_for(gs, "end_turn", {}) == POST_ACTION_DELAY


class _SlyEnergyLagClient:
    """The state reports 0 energy and an unplayable card, then a beat later the
    energy from a discarded Sly card (Tactician: "Sly. Gain 1 Energy") lands.

    Ending the turn on that first snapshot wastes both the energy and the card
    it could have paid for.
    """

    def __init__(self):
        self.actions_posted = []
        self.polls = 0

    def wait_until_ready(self, *a, **kw):
        return {}

    def _state(self, energy):
        return {
            "state_type": "monster",
            "ready_for_command": True,
            "battle": {
                "is_play_phase": True,
                "enemies": [{"entity_id": "E_0", "name": "Foe", "hp": 50, "max_hp": 50,
                             "block": 0, "status": [], "intents": []}],
            },
            "run": {"act": 1, "floor": 1},
            "player": {
                "hp": 70, "max_hp": 70, "gold": 0, "energy": energy, "max_energy": 3,
                "block": 0, "status": [], "potions": [],
                "hand": [{
                    "id": "STRIKE_SILENT", "name": "Strike", "cost": "1", "type": "Attack",
                    "description": "Deal 6 damage.", "index": 0, "target_type": "AnyEnemy",
                    "can_play": energy > 0,
                    "unplayable_reason": None if energy > 0 else "EnergyCostTooHigh",
                }],
                "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
            },
        }

    def get_state(self):
        self.polls += 1
        # First look: no energy. The confirming re-poll sees it arrive.
        return self._state(0 if self.polls <= 1 else 1)

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        return {"status": "ok"}


def test_end_turn_is_confirmed_against_a_fresh_poll(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _SlyEnergyLagClient()
    loop = BotLoop(client=client, max_actions=1)
    loop.run()
    loop.close()

    # The late-arriving energy must be spent, not thrown away by ending early.
    assert client.actions_posted == [("play_card", {"card_index": 0, "target": "E_0"})]


class _PingPongClient:
    """Two screens that bounce off each other forever, each step "succeeding"
    and genuinely changing state -- the rewards <-> card_reward loop. The
    stale-action detector can't see this, since no single decision repeats
    against unchanged state."""

    def __init__(self):
        self.actions_posted = []
        self.on_rewards = True

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        player = {"hp": 50, "max_hp": 70, "gold": 0, "potions": [], "max_potion_slots": 3,
                  "hand": [], "draw_pile": [], "discard_pile": [], "exhaust_pile": []}
        if self.on_rewards:
            return {
                "state_type": "rewards", "ready_for_command": True, "run": {"act": 1, "floor": 5},
                "rewards": {"items": [{"index": 0, "type": "card"}], "can_proceed": True},
                "player": player,
            }
        return {
            "state_type": "card_reward", "ready_for_command": True, "run": {"act": 1, "floor": 5},
            "card_reward": {"cards": [{"index": 0, "name": "Slice"}], "can_skip": True},
            "player": player,
        }

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        if action == "claim_reward":
            self.on_rewards = False
        elif action == "skip_card_reward":
            self.on_rewards = True
        return {"status": "ok"}


def test_ping_pong_between_two_screens_is_broken_by_cycle_detection(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    # Isolate from the rewards-module skip memory so this exercises the
    # loop-level cycle detector rather than the strategy-level fix.
    from bot.strategy import rewards as rewards_mod

    rewards_mod._skipped_card_rewards.clear()
    monkeypatch.setattr(rewards_mod, "_skipped_card_rewards", set())

    client = _PingPongClient()
    loop = BotLoop(client=client, max_actions=20)
    loop.run()
    loop.close()

    assert ("proceed", {}) in client.actions_posted


class _ShivHandClient:
    """Eight 0-cost Shivs and no energy. Every decision is the same "play card
    0", but every play genuinely changes the state -- one fewer Shiv, enemy HP
    down -- which is progress, not a cycle."""

    def __init__(self, shivs=8):
        self.shivs = shivs
        self.enemy_hp = 200
        self.actions_posted = []

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        hand = [
            {"id": "SHIV", "name": "Shiv", "type": "Attack", "cost": "0",
             "description": "Deal 4 damage. Exhaust.", "index": i,
             "target_type": "AnyEnemy", "can_play": True}
            for i in range(self.shivs)
        ]
        return {
            "state_type": "monster",
            "ready_for_command": True,
            "battle": {
                "is_play_phase": True,
                "round": 1,
                "enemies": [{"entity_id": "E_0", "name": "Dummy", "hp": self.enemy_hp,
                             "max_hp": 200, "block": 0, "status": [], "intents": []}],
            },
            "run": {"act": 1, "floor": 1},
            "player": {"hp": 70, "max_hp": 70, "gold": 0, "energy": 0, "max_energy": 3,
                       "block": 0, "status": [], "hand": hand, "potions": [],
                       "max_potion_slots": 3, "draw_pile": [], "discard_pile": [],
                       "exhaust_pile": []},
        }

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        if action == "play_card" and self.shivs:
            self.shivs -= 1
            self.enemy_hp -= 4
        return {"status": "ok"}


def test_a_hand_of_shivs_is_not_mistaken_for_a_cycle(tmp_path, monkeypatch):
    # Keyed on the decision alone, eight identical "play card 0" filled the
    # cycle window and forced an end_turn with a Shiv still in hand -- seven
    # times in one live set, once at the act 2 boss.
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _ShivHandClient(shivs=8)
    loop = BotLoop(client=client, max_actions=12)
    loop.run()
    loop.close()

    before_end = []
    for action, _fields in client.actions_posted:
        if action == "end_turn":
            break
        before_end.append(action)
    assert before_end.count("play_card") == 8


class _StaleRewardsClient:
    """A non-combat screen that never changes -- reproduces the potion-belt-full
    reward loop, where claim_reward returns "ok" but nothing happens. The
    fingerprint check originally only covered combat, so this looped freely."""

    def __init__(self):
        self.actions_posted = []

    def wait_until_ready(self, *a, **kw):
        return {}

    def get_state(self):
        return {
            "state_type": "rewards",
            "ready_for_command": True,
            "rewards": {"items": [{"index": 0, "type": "gold"}], "can_proceed": True},
            "run": {"act": 1, "floor": 1},
            "player": {"hp": 50, "max_hp": 70, "gold": 100, "potions": [], "max_potion_slots": 3},
        }

    def post_action(self, action, **fields):
        self.actions_posted.append((action, fields))
        return {"status": "ok"}


def test_stale_non_combat_action_escapes_with_proceed(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _StaleRewardsClient()
    loop = BotLoop(client=client, max_actions=STALE_ACTION_LIMIT + 2)
    loop.run()
    loop.close()

    # end_turn is not a valid action outside combat -- must escape via proceed.
    assert ("proceed", {}) in client.actions_posted
    assert ("end_turn", {}) not in client.actions_posted


def test_stale_action_forces_end_turn_instead_of_looping_forever(tmp_path, monkeypatch):
    monkeypatch.setattr("bot.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    client = _StaleCombatClient()
    loop = BotLoop(client=client, max_actions=STALE_ACTION_LIMIT + 2)
    loop.run()
    loop.close()

    assert ("end_turn", {}) in client.actions_posted
    # It shouldn't take the whole action budget of identical Strike plays to
    # notice and break out.
    strikes_before_end_turn = 0
    for action, _ in client.actions_posted:
        if action == "end_turn":
            break
        strikes_before_end_turn += 1
    assert strikes_before_end_turn <= STALE_ACTION_LIMIT


# --- ascension guard -------------------------------------------------------

def test_non_zero_ascension_is_flagged_once_per_floor(tmp_path, monkeypatch):
    """Every run must be Ascension 0. The bot cannot select ascension -- the
    API's character_select screen has no such control -- so this is a tripwire
    for the game handing us a different one, which would silently make results
    incomparable with everything already recorded."""
    from bot.game_state import GameState
    from bot.loop import BotLoop

    loop = BotLoop(client=None, max_actions=0)
    logged = []
    monkeypatch.setattr(loop, "_log", lambda rec: logged.append(rec))

    a0 = GameState({"state_type": "map", "run": {"act": 1, "floor": 4, "ascension": 0}, "player": {}})
    a5 = GameState({"state_type": "map", "run": {"act": 1, "floor": 4, "ascension": 5}, "player": {}})

    loop._check_ascension(a0)
    assert logged == []

    loop._check_ascension(a5)
    loop._check_ascension(a5)  # same floor -- must not spam
    assert [r["event"] for r in logged] == ["unexpected_ascension"]
    assert logged[0]["ascension"] == 5
    loop.close()
