"""Scan a decision log for the classes of defect this project keeps finding.

    python scripts/audit_decisions.py [logs/run_YYYYMMDD_HHMMSS.jsonl]

Defaults to the most recently modified per-process log.

Why this exists
---------------
Nearly every real bug found on this project came from a human watching the bot
and noticing one odd play. That does not scale, and the person watching is the
scarce resource. Each check below is a bug that was actually found that way,
turned into something greppable:

* **wasted energy** -- ended a turn holding energy and an affordable card.
* **zero-value play** -- spent energy on a card doing no damage, no block and
  applying nothing (Noxious Fumes as a "lethal", Flechettes with no Skills).
* **missed lethal** -- ended a turn able to kill an enemy outright.
* **wasted draw** -- drew or added Shivs at the hand cap.
* **pointless potion** -- drank a potion that changed nothing that turn.
* **overkill** -- committed far more damage than needed to a kill.
* **unblocked chip** -- ate avoidable damage while holding Block it could
  afford.

None of these are proof of a bug on their own; multi-hit cards, Powers and
setup plays all show up here legitimately. Treat the output as a list of
turns worth replaying, ranked by how often each pattern fires.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.game_state import GameState  # noqa: E402
from bot.strategy import combat as C  # noqa: E402


def _latest_log() -> Path | None:
    # Importing `bot.loop` opens a fresh decision log as a side effect, so
    # analysis scripts leave a trail of empty files behind them. Picking the
    # newest by mtime therefore lands on an empty log and silently reports
    # "nothing found" -- skip anything with no rows.
    logs = [p for p in ROOT.glob("logs/run_*.jsonl") if p.stat().st_size > 0]
    logs.sort(key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def _rows(path: Path):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _card_of(row):
    combat = row.get("combat") or {}
    idx = (row.get("fields") or {}).get("card_index")
    hand = combat.get("hand") or []
    if idx is None or idx >= len(hand):
        return None
    return hand[idx].split("(")[0]


def audit(path: Path):
    findings = collections.defaultdict(list)
    for row in _rows(path):
        raw = row.get("raw")
        state = row.get("state") or {}
        floor = state.get("floor")
        if not raw:
            continue
        try:
            gs = GameState(raw)
        except Exception:
            continue
        if not gs.enemies:
            continue

        action = row.get("action")
        hand = C._playable_hand(gs)
        shiv = C._shiv_damage_bonus(gs)
        strength = C._player_strength(gs)
        live = [e for e in gs.enemies if C._effective_hp(e) > 0]

        # --- ended a turn with energy and something to spend it on ---
        if action == "end_turn":
            # With every enemy dead the fight is already over and `decide()`
            # ends the turn before anything else -- unspent energy there is
            # not waste. Without this guard the check fires on every won
            # fight.
            spendable = [c for c in hand if C._cost_int(c) <= gs.energy]
            if live and gs.energy > 0 and spendable:
                findings["wasted energy"].append(
                    f"floor {floor}: ended turn with {gs.energy} energy and "
                    f"{[c.get('name') for c in spendable][:4]}"
                )
            # --- ended a turn holding a kill ---
            for enemy in live:
                if not isinstance(enemy, dict) or enemy.get("status") is None:
                    continue
                mult = C._player_damage_multiplier(gs, enemy)
                killers = [
                    c for c in hand
                    if C._cost_int(c) <= gs.energy
                    and C._kills_enemy(c, enemy, shiv, hand, 0, strength, mult)
                ]
                if killers:
                    findings["missed lethal"].append(
                        f"floor {floor}: {enemy.get('name')} at "
                        f"{C._damage_to_kill(enemy)} left, held {killers[0].get('name')}"
                    )
                    break

        # --- played a card that did nothing measurable ---
        if action == "play_card":
            name = _card_of(row)
            played = next(
                (c for c in gs.hand if c.get("name") == name), None
            )
            # Every enemy already doomed by poison: our damage changes
            # nothing, the tick will finish them. Worth spending the energy
            # elsewhere (block, setup) rather than on a corpse.
            if played is not None and not live and C._is_attack_option(played):
                findings["attacked a doomed enemy"].append(
                    f"floor {floor}: {name} into an enemy poison will kill anyway"
                )

            if played is not None and C._cost_int(played) > 0 and live:
                target = C._lowest_hp_enemy(live)
                dmg = (
                    C._effective_damage(played, target, shiv, gs.hand, strength)
                    if target is not None else 0
                )
                block = C._effective_block(played, C._player_dexterity(gs))
                description = played.get("description", "") or ""
                does_something = bool(
                    dmg > 0
                    or block > 0
                    or C._card_applies_weak(played)
                    or C._card_applies_vulnerable(played)
                    or C._card_poison(played) > 0
                    or (played.get("type") or "").lower() == "power"
                    or C._DRAWS_RE.search(description)
                    or C._GAINS_ENERGY_RE.search(description)
                    or C._card_shiv_count(played) > 0
                    or C._strength_down_amount(played) > 0
                    or "exhaust" in description.lower()
                    # Playing a held Status card to delete its recurring
                    # damage (Beckon: "lose 6 HP") is a purge, not a wasted
                    # energy -- it is the whole point of step 1.6.
                    or C._self_damage_in_hand(played) > 0
                    # Cards bought for a buff that lands later (Shadow Step).
                    or C._grants_lasting_buff(played)
                )
                if not does_something:
                    findings["zero-value play"].append(
                        f"floor {floor}: played {name} for "
                        f"{C._cost_int(played)} energy, no damage/block/debuff"
                    )
            # --- added cards to a hand that cannot hold them ---
            if played is not None and len(gs.hand) >= C.HAND_LIMIT:
                if C._card_shiv_count(played) > 0 or C._DRAWS_RE.search(
                    played.get("description", "") or ""
                ):
                    findings["wasted draw"].append(
                        f"floor {floor}: {name} with {len(gs.hand)} cards in hand"
                    )
            # --- massive overkill on a kill ---
            if played is not None and live:
                target = C._lowest_hp_enemy(live)
                if target is None:
                    continue
                need = C._damage_to_kill(target)
                dealt = C._effective_damage(played, target, shiv, gs.hand, strength)
                if need > 0 and dealt >= need * 3 and dealt - need >= 15:
                    findings["overkill"].append(
                        f"floor {floor}: {name} dealt ~{dealt:.0f} into a "
                        f"{need} HP target"
                    )

        # --- potion that changed nothing ---
        if action == "use_potion":
            pots = (row.get("options") or {}).get("potions") or []
            slot = (row.get("fields") or {}).get("slot")
            pot = next((p for p in pots if p.get("slot") == slot), None)
            if pot is not None:
                combat = row.get("combat") or {}
                unblocked = combat.get("unblocked", 0)
                text = (pot.get("text") or "").lower()
                defensive = "block" in text or "heal" in text
                if defensive and unblocked == 0:
                    findings["pointless potion"].append(
                        f"floor {floor}: {pot.get('name')} with nothing unblocked"
                    )
    return findings


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_log()
    if not path or not path.exists():
        print("No decision log found under logs/.")
        return
    findings = audit(path)
    total = sum(len(v) for v in findings.values())
    print(f"{path.name} - {total} suspicious decisions\n")
    if not total:
        print("  Nothing flagged.")
        return
    for kind, items in sorted(findings.items(), key=lambda kv: -len(kv[1])):
        print(f"  {kind}: {len(items)}")
        for line in items[:4]:
            print(f"      {line}")
        if len(items) > 4:
            print(f"      ... and {len(items) - 4} more")
        print()
    print("  These are candidates, not proof. Replay one before believing it.")


if __name__ == "__main__":
    main()
