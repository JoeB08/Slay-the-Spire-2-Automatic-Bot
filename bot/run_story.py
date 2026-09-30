"""A readable account of each finished run, written by the bot itself.

The decision log (`logs/run_*.jsonl`) holds everything but is machine-shaped:
one JSON payload per decision. This turns one run's slice of it into Markdown
a person can read without help -- what the run was carrying, how HP moved
floor by floor, every elite and boss fight turn by turn, and a short list of
moments worth a second look -- so misplays can be spotted without anyone
querying the logs. Stories are written to `logs/stories/`, with an INDEX.md
listing every run.

Nothing here decides anything. It only reads what the bot already logged.
The recorder calls it inside a try/except, so a story can never stop a run.
Regenerate stories for any folder of runs with `scripts/run_story.py`.
"""
from __future__ import annotations

import datetime
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from . import log_io

STORIES_DIR = "stories"
INDEX_FILE = "INDEX.md"
COMBAT_TYPES = ("monster", "elite", "boss")
# An enemy turn costing this share of max HP is worth a second look.
BIG_HIT_FRACTION = 0.25
# The log's first row of a run is written a moment before the recorder stamps
# the run's start, so the window is widened slightly.
WINDOW_SLACK_S = 5.0
MAX_FLAGS_SHOWN = 30

_BLOCK_RE = re.compile(r"gain (\d+) block", re.IGNORECASE)
_HAND_CARD_RE = re.compile(r"^([^(]+)")


# --- reading ---------------------------------------------------------------

def _rows(log_path: Path, t0: float, t1: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # The log may have been compressed since it was written -- find_log matches
    # either form, and open_log handles the BOM a PowerShell re-save leaves.
    found = log_io.find_log(log_path)
    if found is None:
        return rows
    with log_io.open_log(found) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = row.get("ts") or 0
            if t0 - WINDOW_SLACK_S <= ts <= t1 + WINDOW_SLACK_S:
                rows.append(row)
    return rows


def _cost(card: dict[str, Any]) -> int:
    try:
        return int(card.get("cost"))
    except (TypeError, ValueError):
        return 0


def _names(cards: list[dict[str, Any]]) -> str:
    return ", ".join(c.get("name") or "?" for c in cards)


def _counted(names: list[str]) -> str:
    counts = Counter(names)
    return ", ".join(f"{n} x{c}" if c > 1 else n for n, c in sorted(counts.items()))


def _statuses(entity: dict[str, Any]) -> str:
    return ", ".join(
        f"{s.get('name')} {s.get('amount')}".strip() for s in entity.get("status") or []
        if s.get("name")
    )


def _intents(enemy: dict[str, Any]) -> str:
    parts = []
    for intent in enemy.get("intents") or []:
        label = f"{intent.get('type') or ''} {intent.get('label') or ''}".strip()
        if label:
            parts.append(label)
    return " + ".join(parts)


def _enemy_line(enemy: dict[str, Any]) -> str:
    text = f"{enemy.get('name')} {enemy.get('hp')}/{enemy.get('max_hp')}"
    statuses = _statuses(enemy)
    if statuses:
        text += f" [{statuses}]"
    intents = _intents(enemy)
    if intents:
        text += f" -- {intents}"
    return text


def _card_archetype(name: str) -> Optional[str]:
    """Which engine a card belongs to, from the bot's own card data."""
    from .strategy import cards as card_db

    tags = set((card_db.card_info(name) or {}).get("tags") or [])
    for archetype, archetype_tags in card_db.ARCHETYPE_TAGS.items():
        if tags & set(archetype_tags):
            return archetype
    return None


def _label(name: Optional[str]) -> str:
    """A card name tagged with its archetype -- "Accuracy [shiv]"."""
    if not name:
        return "?"
    archetype = _card_archetype(name)
    return f"{name} [{archetype}]" if archetype else name


def _archetype_line(record: dict[str, Any]) -> str:
    """The engine the deck was built toward, as the bot itself judges it."""
    from .strategy import cards as card_db

    deck = record.get("deck") or []
    relics = [{"name": n} for n in record.get("relics") or []]
    target = card_db.dominant_archetype(deck, relics)
    chosen = [n for n in deck if card_db.base_name(n) not in card_db.STARTERS_BY_NAME]
    lean = Counter(a for a in (_card_archetype(n) for n in chosen) if a)
    held = ", ".join(f"{a} {c}" for a, c in lean.most_common()) or "no archetype cards"
    return f"- **Target archetype:** {target or 'none committed'} (cards held: {held})"


def _state(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("state") or {}


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("raw") or {}


def _options(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("options") or {}


# --- describing single actions ---------------------------------------------

def _played_card(row: dict[str, Any]) -> Optional[str]:
    idx = (row.get("fields") or {}).get("card_index")
    if idx is None:
        return None
    hand = (_raw(row).get("player") or {}).get("hand") or []
    if 0 <= idx < len(hand):
        return hand[idx].get("name")
    summary = (row.get("combat") or {}).get("hand") or []
    if 0 <= idx < len(summary):
        m = _HAND_CARD_RE.match(summary[idx])
        return m.group(1) if m else summary[idx]
    return None


def _potion_name(row: dict[str, Any]) -> str:
    slot = (row.get("fields") or {}).get("slot")
    for potion in (_raw(row).get("player") or {}).get("potions") or []:
        if potion.get("slot") == slot:
            return potion.get("name") or "a potion"
    return "a potion"


def _combat_action(row: dict[str, Any], names: dict[str, str]) -> Optional[str]:
    action = row.get("action")
    fields = row.get("fields") or {}
    if action == "play_card":
        card = _played_card(row) or "a card"
        target = fields.get("target")
        return f"{card} -> {names.get(target, target)}" if target else card
    if action == "use_potion":
        return f"drank {_potion_name(row)}"
    if action == "end_turn":
        return "ended turn"
    if action == "combat_select_card":
        offered = _options(row).get("offered") or []
        idx = fields.get("card_index")
        card = next((o.get("name") for o in offered if o.get("index") == idx), "a card")
        prompt = (_options(row).get("prompt") or "").lower()
        for verb in ("discard", "exhaust", "retain", "upgrade"):
            if verb in prompt:
                return f"{verb}ed {card}".replace("discarded", "discarded").replace("retained", "kept")
        return f"chose {card}"
    return None


# --- flags -----------------------------------------------------------------

def _end_turn_flag(row: dict[str, Any]) -> Optional[str]:
    """Energy or playable cards left at the end of a turn -- worth a look."""
    raw = _raw(row)
    player = raw.get("player") or {}
    energy = player.get("energy") or 0
    playable = [
        c for c in player.get("hand") or []
        if c.get("can_play")
        and (c.get("type") or "").lower() not in ("status", "curse")
        and _cost(c) <= energy
    ]
    if not playable:
        return None
    from .strategy import combat  # imported lazily: this module must stay light

    enemies = (raw.get("battle") or {}).get("enemies") or []
    # A turn ended by the kill has nothing left to spend on. Set 3, run 14:
    # Strike + Neutralize finished Phrog Parasite and the end-turn row, with
    # two Defends and Survivor in hand and no enemy standing, was flagged.
    if not any((e.get("hp") or 0) > 0 for e in enemies):
        return None
    incoming = combat._incoming_damage(enemies)
    block = player.get("block") or 0
    blockers = [c for c in playable if _BLOCK_RE.search(c.get("description") or "")]
    if incoming > block and blockers:
        return (f"ended the turn with {incoming - block} damage coming unblocked "
                f"while holding {_names(blockers)}")
    return f"ended the turn with {energy} energy and {_names(playable)} still playable"


# --- the story ---------------------------------------------------------------

def _group_by_floor(rows: list[dict[str, Any]]) -> list[tuple[int, list[dict[str, Any]]]]:
    groups: list[tuple[int, list[dict[str, Any]]]] = []
    for row in rows:
        floor = _state(row).get("floor")
        if not floor:
            if groups and row.get("event"):
                groups[-1][1].append(row)  # an event belongs to where it happened
            continue
        if not groups or groups[-1][0] != floor:
            groups.append((floor, []))
        groups[-1][1].append(row)
    return groups


def _floor_kind(rows: list[dict[str, Any]]) -> str:
    types = [_state(r).get("state_type") for r in rows if _state(r).get("state_type")]
    for kind in ("boss", "elite", "monster"):
        if kind in types:
            return kind
    for kind in ("rest_site", "shop", "event", "treasure"):
        if kind in types:
            return kind.replace("_", " ")
    return types[0] if types else "?"


def _floor_notes(rows: list[dict[str, Any]], kind: str) -> str:
    notes: list[str] = []
    if kind in COMBAT_TYPES:
        first = next((r for r in rows if _raw(r).get("battle")), None)
        if first:
            enemies = [e.get("name") or "?" for e in _raw(first)["battle"].get("enemies") or []]
            notes.append(_counted(enemies))
        rounds = [(_raw(r).get("battle") or {}).get("round") for r in rows]
        rounds = [x for x in rounds if isinstance(x, int)]
        if rounds:
            notes.append(f"{max(rounds)} rounds")
    for row in rows:
        st = _state(row).get("state_type")
        action = row.get("action")
        fields = row.get("fields") or {}
        opts = _options(row)
        if st == "event" and action == "choose_event_option":
            chosen = next((o for o in opts.get("options") or [] if o.get("index") == fields.get("index")), None)
            if chosen and not chosen.get("is_proceed"):
                notes.append(f"{opts.get('event') or 'event'}: chose {chosen.get('title')}")
        elif st == "rest_site" and action == "choose_rest_option":
            chosen = next((o for o in opts.get("options") or [] if o.get("index") == fields.get("index")), None)
            if chosen:
                notes.append(f"rest site: {chosen.get('name')}")
        elif st == "shop" and action == "shop_purchase":
            item = next((i for i in opts.get("items") or [] if i.get("index") == fields.get("index")), None)
            if item:
                label = _label(item.get("name")) if item.get("name") else \
                    item.get("category", "item").replace("_", " ")
                notes.append(f"bought {label} ({item.get('price')}g)")
        elif st == "card_reward" and action in ("select_card_reward", "skip_card_reward"):
            offered = [_label(c.get("name")) for c in opts.get("cards") or []]
            if action == "skip_card_reward":
                notes.append(f"skipped a card reward ({' / '.join(offered)})")
            else:
                pick = next((c.get("name") for c in opts.get("cards") or []
                             if c.get("index") == fields.get("card_index")), None)
                notes.append(f"took {_label(pick)} (offered {' / '.join(offered)})")
        elif st == "card_select" and action == "select_card":
            offered = opts.get("offered") or []
            card = next((o.get("name") for o in offered if o.get("index") == fields.get("index")), None)
            if card:
                notes.append(f"{(opts.get('prompt') or 'chose').rstrip('.')}: {card}")
    return "; ".join(n for n in notes if n)


def _fight_detail(rows: list[dict[str, Any]], hp_after_floor: Optional[int]) -> list[str]:
    rounds: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    current: Optional[int] = None
    for row in rows:
        rnd = (_raw(row).get("battle") or {}).get("round")
        if isinstance(rnd, int):
            current = rnd
        if current is None or row.get("event"):
            continue
        if current not in rounds:
            rounds[current] = []
            order.append(current)
        rounds[current].append(row)

    lines: list[str] = []
    starts: list[Optional[int]] = []
    for rnd in order:
        first = next((r for r in rounds[rnd] if _raw(r).get("battle")), None)
        starts.append((_raw(first).get("player") or {}).get("hp") if first else None)
    for i, rnd in enumerate(order):
        first = next((r for r in rounds[rnd] if _raw(r).get("battle")), None)
        if first is None:
            continue
        player = _raw(first).get("player") or {}
        enemies = _raw(first)["battle"].get("enemies") or []
        names = {e.get("entity_id"): e.get("name") for e in enemies}
        lines.append(f"**Round {rnd}** -- HP {player.get('hp')}, Block {player.get('block') or 0}, "
                     f"Energy {player.get('energy')}")
        alive = [e for e in enemies if (e.get("hp") or 0) > 0]
        lines.append("- Enemies: " + ("; ".join(_enemy_line(e) for e in alive) or "none"))
        own = _statuses(player)
        if own:
            lines.append(f"- You: {own}")
        hand = player.get("hand") or []
        lines.append("- Hand: " + (", ".join(f"{c.get('name')} ({c.get('cost')})" for c in hand) or "empty"))
        acts = [a for a in (_combat_action(r, names) for r in rounds[rnd]) if a]
        lines.append("- Played: " + ("; ".join(acts) if acts else "nothing"))
        before = starts[i]
        after = starts[i + 1] if i + 1 < len(order) else hp_after_floor
        if isinstance(before, int) and isinstance(after, int) and after != before:
            change = before - after
            lines.append(f"- Then: HP {before} -> {after} "
                         f"({'took ' + str(change) if change > 0 else 'healed ' + str(-change)})")
        lines.append("")
    return lines


def _killer(record: dict[str, Any]) -> str:
    killed = record.get("killed_by") or {}
    enemies = killed.get("enemies") or []
    return _counted([e.get("name") or "?" for e in enemies]) or "unknown"


def _headline(record: dict[str, Any]) -> str:
    killed = record.get("killed_by") or {}
    where = killed.get("state_type")
    role = {"boss": f"act {record.get('act_reached')} boss", "elite": "elite"}.get(where, "")
    verb = "died" if record.get("outcome") == "death" else "ended"
    return (f"{verb} on floor {record.get('floor_reached')} to {_killer(record)}"
            + (f" ({role})" if role else ""))


def build_story(record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """The Markdown story, and a few facts for the index line."""
    started = datetime.datetime.fromtimestamp(record.get("started_ts") or 0)
    minutes = round((record.get("duration_s") or 0) / 60)
    lines = [f"# Run -- {_headline(record)}", ""]
    lines.append(f"Played {started:%Y-%m-%d %H:%M}, {minutes} min. Ascension "
                 f"{record.get('ascension', 0)}, {record.get('decisions')} decisions.")
    lines.append("")
    lines.append(f"- **Reached:** act {record.get('act_reached')}, floor {record.get('floor_reached')}. "
                 f"Lowest HP {record.get('hp_low_water')}, damage taken {record.get('damage_taken')}.")
    lines.append(f"- **Final deck ({record.get('deck_size')}):** {_counted(record.get('deck') or [])}")
    lines.append(f"- **Relics:** {', '.join(record.get('relics') or []) or 'none'}")
    try:
        lines.append(_archetype_line(record))
    except Exception:  # noqa: BLE001 -- card data is optional here
        pass
    potions = record.get("potions") or []
    lines.append(f"- **Potions at the end:** {', '.join(potions) or 'none'}")
    lines.append("")

    groups = _group_by_floor(rows)
    flags: list[str] = []
    table = ["| Floor | What | HP | Notes |", "|---:|---|---|---|"]
    detail: list[str] = []
    entered_final: Optional[int] = None
    max_hp = record.get("max_hp") or 1
    for gi, (floor, frows) in enumerate(groups):
        kind = _floor_kind(frows)
        hps = [_state(r).get("hp") for r in frows if isinstance(_state(r).get("hp"), int)
               and not r.get("event")]
        hp_in = hps[0] if hps else None
        if gi + 1 < len(groups):
            nxt = [_state(r).get("hp") for r in groups[gi + 1][1] if isinstance(_state(r).get("hp"), int)]
            hp_out = nxt[0] if nxt else (hps[-1] if hps else None)
        else:
            hp_out = record.get("final_hp") if record.get("final_hp") is not None else (hps[-1] if hps else None)
        change = ""
        if isinstance(hp_in, int) and isinstance(hp_out, int):
            change = f"{hp_in} -> {hp_out}"
            if hp_in - hp_out >= max_hp * BIG_HIT_FRACTION:
                change += " **big loss**"
        table.append(f"| {floor} | {kind} | {change} | {_floor_notes(frows, kind)} |")

        # Flags from this floor.
        last_round_hp: Optional[int] = None
        last_round: Optional[int] = None
        errors = 0
        for row in frows:
            if row.get("event") in ("cycle_detected", "stale_action_detected"):
                flags.append(f"Floor {floor}: the loop's safety check forced the turn to end "
                             f"({row['event'].replace('_', ' ')}).")
                continue
            if row.get("event") == "stuck_detected":
                flags.append(f"Floor {floor}: the game froze and was relaunched.")
                continue
            if row.get("result") == "error":
                errors += 1
            battle = _raw(row).get("battle") or {}
            rnd = battle.get("round")
            hp = (_raw(row).get("player") or {}).get("hp")
            if isinstance(rnd, int) and isinstance(hp, int) and rnd != last_round:
                if last_round_hp is not None and last_round_hp - hp >= max_hp * BIG_HIT_FRACTION:
                    flags.append(f"Floor {floor}, round {rnd}: took {last_round_hp - hp} in one enemy turn.")
                last_round, last_round_hp = rnd, hp
            if row.get("action") == "end_turn" and _raw(row).get("battle"):
                flag = _end_turn_flag(row)
                if flag:
                    flags.append(f"Floor {floor}, round {rnd}: {flag}.")
        if errors >= 3:
            flags.append(f"Floor {floor}: {errors} actions were rejected by the game.")

        final_floor = gi == len(groups) - 1
        if kind in ("elite", "boss") or (final_floor and kind in COMBAT_TYPES):
            first = next((r for r in frows if _raw(r).get("battle")), None)
            player = (_raw(first).get("player") or {}) if first else {}
            title = "The fight that ended the run" if final_floor else f"{kind.title()} fight"
            detail.append(f"### Floor {floor} -- {title}")
            detail.append(f"Entered at {player.get('hp')}/{player.get('max_hp')} HP"
                          + (f", potions: {_names(player.get('potions') or [])}" if player.get("potions") else "")
                          + ".")
            detail.append("")
            detail.extend(_fight_detail(frows, hp_out))
            if final_floor:
                entered_final = player.get("hp")

    if potions and record.get("outcome") == "death":
        flags.append(f"Died holding {', '.join(potions)}.")

    lines.append("## Worth a look")
    lines.append("")
    lines.append("Found automatically from the log -- hints, not verdicts. Each is a turn "
                 "worth checking in the fights below.")
    lines.append("")
    if flags:
        for flag in flags[:MAX_FLAGS_SHOWN]:
            lines.append(f"- {flag}")
        if len(flags) > MAX_FLAGS_SHOWN:
            lines.append(f"- ...and {len(flags) - MAX_FLAGS_SHOWN} more.")
    else:
        lines.append("- Nothing flagged.")
    lines.append("")
    lines.append("## Floor by floor")
    lines.append("")
    lines.extend(table)
    lines.append("")
    lines.append("## Elite and boss fights, turn by turn")
    lines.append("")
    lines.extend(detail or ["No elite or boss fight in this run."])
    if not rows:
        lines.append("")
        lines.append("_The decision log for this run was not found, so only the summary is shown._")
    facts = {"flags": len(flags), "entered_final": entered_final}
    return "\n".join(lines) + "\n", facts


def _filename(record: dict[str, Any]) -> str:
    ended = datetime.datetime.fromtimestamp(record.get("ended_ts") or 0)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", _killer(record)).strip("-")[:40] or "unknown"
    return f"{ended:%Y%m%d_%H%M}_floor{record.get('floor_reached', 0):02d}_{slug}.md"


def _index_line(n: int, record: dict[str, Any], facts: dict[str, Any], filename: str) -> str:
    ended = datetime.datetime.fromtimestamp(record.get("ended_ts") or 0)
    entered = facts.get("entered_final")
    entered_text = f"{entered}/{record.get('max_hp')}" if entered is not None else "?"
    return (f"| {n} | {ended:%m-%d %H:%M} | {record.get('floor_reached')} | {record.get('act_reached')} "
            f"| {_killer(record)} | {entered_text} | {facts.get('flags')} | [story]({filename}) |")


_INDEX_HEADER = [
    "# Runs",
    "",
    "One line per finished run, newest last. HP is what the run carried into the fight that "
    "ended it; flags are the moments each story marks as worth a look.",
    "",
    "| # | Ended | Floor | Act | Died to | HP into last fight | Flags | Story |",
    "|---:|---|---:|---:|---|---|---:|---|",
]


def write_story(log_path: Path, record: dict[str, Any], out_dir: Path) -> Path:
    """Write one run's story and add it to the index. Returns the story path."""
    rows = _rows(Path(log_path), record.get("started_ts") or 0, record.get("ended_ts") or 0)
    story, facts = build_story(record, rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _filename(record)
    path.write_text(story, encoding="utf-8")

    index = out_dir / INDEX_FILE
    if index.exists():
        existing = index.read_text(encoding="utf-8").splitlines()
    else:
        existing = list(_INDEX_HEADER)
    n = sum(1 for line in existing if line.startswith("| ") and not line.startswith("| #")
            and not line.startswith("|---")) + 1
    existing.append(_index_line(n, record, facts, path.name))
    index.write_text("\n".join(existing) + "\n", encoding="utf-8")
    return path
