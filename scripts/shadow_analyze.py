"""Compare a human-played run against what the bot would have done.

Reads a `shadow_record.py` capture and reconstructs the *human's* choice at
each decision point by diffing consecutive recorded states, then reports where
the bot would have chosen differently. Divergences are the interesting part:
each one is either a place the bot is wrong, or a place it is right and the
human wasn't -- both worth reading.

Reconstruction is per screen and deliberately conservative. Anything it cannot
be determined with confidence is reported as "unclear" rather than guessed at,
so the divergence list stays trustworthy.

Each screen uses whatever signal the payload actually carries, established by
inspecting a real capture rather than assumed:

  combat      a card leaves the hand between two snapshots
  card_reward the deck (visible only in combat) gains one of the offered cards
  map         the next non-map screen reveals the node type taken
  hand_select `hand_select.selected_cards` grows by one
  shop        an item flips `is_stocked` true -> false
  rest_site   HP jumps (Rest), or the deck gains a "+" card (Smith)
  rewards     the `rewards.items` list loses an entry
  event       `option.was_chosen` -- true only briefly, so it survives on a
              minority of polls and most events stay unclear

Usage:
    python scripts/shadow_analyze.py logs/shadow_20260828_213000.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COMBAT = ("monster", "elite", "boss")

# A known difference in philosophy, not a defect: the human ends the turn
# holding cards when nothing is attacking, while the bot's leftover-energy rule
# plays everything it can ("energy doesn't carry over"). Counting these as
# disagreements buries the real ones -- but silently dropping them would hide a
# genuine strategic question, so they get their own bucket.
STYLISTIC = "stylistic:leftover-play-vs-hold"


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _raw(rec: dict) -> dict:
    return rec.get("raw") or {}


def _player(rec: dict) -> dict:
    return _raw(rec).get("player") or {}


def _screen(rec: dict) -> str:
    return (rec.get("state") or {}).get("state_type") or "?"


def _hand(rec: dict) -> list[str]:
    return [c.get("name") for c in _player(rec).get("hand", [])]


def _energy(rec: dict) -> int:
    return _player(rec).get("energy", 0)


def _deck_seen(rec: dict) -> list[str] | None:
    """The full deck, visible only during combat (draw + discard + hand + exhaust)."""
    player = _player(rec)
    piles = ("draw_pile", "discard_pile", "hand", "exhaust_pile")
    if not any(player.get(p) for p in piles):
        return None
    out: list[str] = []
    for p in piles:
        out += [c.get("name") for c in (player.get(p) or [])]
    return sorted(out)


def _next_same(recs: list[dict], i: int, screen: str, span: int = 8) -> dict | None:
    for r in recs[i + 1: i + 1 + span]:
        if _screen(r) == screen:
            return r
    return None


def _next_other(recs: list[dict], i: int, screen: str, span: int = 14) -> dict | None:
    for r in recs[i + 1: i + 1 + span]:
        if _screen(r) != screen:
            return r
    return None


# --- per-screen reconstruction -------------------------------------------

def _has_nothing_to_decide(rec: dict) -> bool:
    """True for a record captured *after* the choice was already made.

    A one-shot screen keeps being polled once it has been used -- a spent
    campfire still reports `state_type: rest_site`, but with an empty options
    list and the bot correctly saying `proceed`. Comparing the human's earlier
    choice against that trailing record scored a disagreement every time and
    made rest sites look like 60% agreement when the first record of each
    visit actually matched.
    """
    st = _screen(rec)
    section = _raw(rec).get(st)
    if not isinstance(section, dict):
        return False
    for key in ("options", "items", "cards"):
        if key in section:
            return not section.get(key)
    return False


def _enemy_is_attacking(rec: dict) -> bool:
    from bot.strategy import combat as combat_mod
    enemies = ((_raw(rec).get("battle") or {}).get("enemies") or [])
    return combat_mod._incoming_damage(enemies) > 0


def _combat(recs, i, rec):
    nxt = recs[i + 1] if i + 1 < len(recs) else None
    bot = rec.get("bot_action")
    if bot == "play_card":
        idx = (rec.get("bot_fields") or {}).get("card_index")
        hand = _hand(rec)
        bot = hand[idx] if idx is not None and idx < len(hand) else None
    human = None
    if nxt is not None and _screen(nxt) == _screen(rec):
        gone = Counter(_hand(rec)) - Counter(_hand(nxt))
        if len(gone) == 1 and sum(gone.values()) == 1:
            human = next(iter(gone))
        elif _energy(nxt) == _energy(rec) and len(_hand(nxt)) == len(_hand(rec)):
            human = "end_turn"
    return human, bot


def _card_reward(recs, i, rec, deck_points):
    offered = [c.get("name") for c in ((rec.get("options") or {}).get("cards") or [])]
    idx = (rec.get("bot_fields") or {}).get("card_index")
    bot = offered[idx] if idx is not None and idx < len(offered) else "skip"
    after = next((d for j, d in deck_points if j > i), None)
    before = next((d for j, d in reversed(deck_points) if j < i), None)
    human = None
    if after is not None and before is not None:
        gained = list((Counter(after) - Counter(before)).elements())
        taken = [g for g in gained if g in offered]
        human = taken[0] if taken else ("skip" if not gained else None)
    return human, bot


def _norm(x):
    return (x or "").replace("_", "").lower() or None


def _map(recs, i, rec):
    human = _norm(next(
        (s for s in (_screen(r) for r in recs[i + 1: i + 6]) if s and s != "map"), None
    ))
    opts = (rec.get("options") or {}).get("next_options") or []
    bidx = (rec.get("bot_fields") or {}).get("index")
    bot = _norm(next((o.get("type") for o in opts if o.get("index") == bidx), None))
    return human, bot


def _hand_select(recs, i, rec):
    cur = _raw(rec).get("hand_select") or {}
    offered = {c.get("index"): c.get("name") for c in (cur.get("cards") or [])}
    bidx = (rec.get("bot_fields") or {}).get("card_index")
    bot = (offered.get(bidx) if rec.get("bot_action") == "combat_select_card"
           else rec.get("bot_action"))

    before = {c.get("index") for c in (cur.get("selected_cards") or [])}
    human = None
    nxt = _next_same(recs, i, "hand_select")
    if nxt is not None:
        after = {c.get("index") for c in
                 ((_raw(nxt).get("hand_select") or {}).get("selected_cards") or [])}
        new = after - before
        if len(new) == 1:
            human = offered.get(next(iter(new)))
        elif not after and before:
            human = "combat_confirm_selection"
    return human, bot


def _item_name(it: dict):
    return (it.get("card_name") or it.get("relic_name")
            or it.get("potion_name") or it.get("category"))


def _shop(recs, i, rec):
    items = {it["index"]: it for it in ((_raw(rec).get("shop") or {}).get("items") or [])}
    bidx = (rec.get("bot_fields") or {}).get("index")
    bot = (_item_name(items[bidx]) if rec.get("bot_action") == "shop_purchase" and bidx in items
           else rec.get("bot_action"))

    human = None
    nxt = _next_same(recs, i, "shop")
    if nxt is not None:
        later = {it["index"]: it for it in ((_raw(nxt).get("shop") or {}).get("items") or [])}
        bought = [k for k, it in items.items()
                  if it.get("is_stocked") and later.get(k, {}).get("is_stocked") is False]
        if len(bought) == 1:
            human = _item_name(items[bought[0]])
    return human, bot


def _rest_site(recs, i, rec, deck_points):
    opts = {o.get("index"): o for o in ((_raw(rec).get("rest_site") or {}).get("options") or [])}
    bidx = (rec.get("bot_fields") or {}).get("index")
    bot = (opts.get(bidx) or {}).get("id") if bidx in opts else rec.get("bot_action")

    human = None
    nxt = _next_other(recs, i, "rest_site")
    if nxt is not None:
        hp0, hp1 = _player(rec).get("hp"), _player(nxt).get("hp")
        if hp0 is not None and hp1 is not None and hp1 > hp0:
            human = "HEAL"
        else:
            # No heal. A Smith shows up as one more upgraded card in the deck
            # the next time the deck is visible (i.e. in combat).
            before = next((d for j, d in reversed(deck_points) if j < i), None)
            after = next((d for j, d in deck_points if j > i), None)
            if before is not None and after is not None:
                ups = lambda deck: sum(1 for c in deck if (c or "").endswith("+"))
                if ups(after) > ups(before):
                    human = "SMITH"
    return human, bot


def _rewards(recs, i, rec):
    items = {it["index"]: (it.get("type") or it.get("description"))
             for it in ((_raw(rec).get("rewards") or {}).get("items") or [])}
    bidx = (rec.get("bot_fields") or {}).get("index")
    bot = items.get(bidx) if rec.get("bot_action") == "claim_reward" else rec.get("bot_action")

    human = None
    nxt = _next_same(recs, i, "rewards")
    if nxt is not None:
        later = {it["index"] for it in ((_raw(nxt).get("rewards") or {}).get("items") or [])}
        gone = [k for k in items if k not in later]
        if len(gone) == 1:
            human = items[gone[0]]
    return human, bot


def _event(recs, i, rec):
    ev = _raw(rec).get("event") or {}
    opts = {o.get("index"): (o.get("title") or o.get("description"))
            for o in (ev.get("options") or [])}
    bidx = (rec.get("bot_fields") or {}).get("index")
    bot = opts.get(bidx) if rec.get("bot_action") == "choose_event_option" else rec.get("bot_action")

    human = None
    for r in recs[i: i + 6]:
        if _screen(r) != "event":
            break
        for o in ((_raw(r).get("event") or {}).get("options") or []):
            if o.get("was_chosen"):
                human = o.get("title") or o.get("description")
                break
        if human:
            break
    return human, bot


def main() -> None:
    parser = argparse.ArgumentParser(description="Human vs bot decision comparison")
    parser.add_argument("capture", help="a shadow_*.jsonl produced by shadow_record.py")
    parser.add_argument("--show", type=int, default=40, help="max divergences to print per screen")
    args = parser.parse_args()

    recs = _load(Path(args.capture))
    if not recs:
        print("Nothing recorded.")
        return

    agree: Counter = Counter()
    differ: Counter = Counter()
    unknown: Counter = Counter()
    stylistic: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    repeats: dict[str, Counter] = defaultdict(Counter)

    deck_points = [(i, d) for i, d in ((i, _deck_seen(r)) for i, r in enumerate(recs)) if d]

    one_shot_seen: set = set()
    for i, rec in enumerate(recs):
        st = _screen(rec)
        if _has_nothing_to_decide(rec):
            continue
        # Screens whose choice is made once per visit: only the first record
        # is a decision, the rest are the same screen still being polled.
        if st in ("rest_site", "card_reward", "event"):
            visit = (st, (rec.get("state") or {}).get("floor"))
            if visit in one_shot_seen:
                continue
            one_shot_seen.add(visit)
        if st in COMBAT:
            human, bot = _combat(recs, i, rec)
            # Human ended the turn with cards still playable and nothing
            # incoming; the bot would have spent them. Bucket it separately.
            if (human == "end_turn" and bot not in (None, "end_turn")
                    and not _enemy_is_attacking(rec)):
                stylistic[st] += 1
                continue
        elif st == "card_reward":
            human, bot = _card_reward(recs, i, rec, deck_points)
        elif st == "map":
            human, bot = _map(recs, i, rec)
        elif st == "hand_select":
            human, bot = _hand_select(recs, i, rec)
        elif st == "shop":
            human, bot = _shop(recs, i, rec)
        elif st == "rest_site":
            human, bot = _rest_site(recs, i, rec, deck_points)
        elif st == "rewards":
            human, bot = _rewards(recs, i, rec)
        elif st == "event":
            human, bot = _event(recs, i, rec)
        else:
            human, bot = None, rec.get("bot_action")

        if human is None or bot is None:
            unknown[st] += 1
        elif str(human) == str(bot):
            agree[st] += 1
        else:
            floor = (rec.get("state") or {}).get("floor")
            row = f"    f{floor:<3} you: {human!s:26} bot: {bot!s}"
            prev = examples[st][-1] if examples[st] else None
            # The bot's recommendation is recomputed every state, so one the
            # human never takes is re-offered on every later decision of that
            # fight. Counting each as a fresh disagreement biases the table one
            # way only -- elites once scored 13% almost entirely on that.
            if (prev is not None and prev.split("bot:")[-1] == row.split("bot:")[-1]
                    and prev.startswith(f"    f{floor:<3}")):
                repeats[st][len(examples[st]) - 1] += 1
            else:
                differ[st] += 1
                examples[st].append(row)

    print(f"=== {len(recs)} decision points from {args.capture} ===\n")
    if stylistic:
        total_sty = sum(stylistic.values())
        print(f"Excluded as stylistic ({STYLISTIC}): {total_sty} decisions")
        print("  -- human ended the turn holding cards while nothing was attacking;")
        print("     the bot's leftover-energy rule would have played them. Reported")
        print("     separately so it neither counts against the bot nor hides a")
        print("     real strategic question.")
        for k, v in stylistic.most_common():
            print(f"     {k}: {v}")
        print()
    print(f"{'screen':14} {'same':>6} {'differ':>7} {'unclear':>8}   agreement")
    ta = td = 0
    for key in sorted(set(agree) | set(differ) | set(unknown)):
        a, d, u = agree[key], differ[key], unknown[key]
        ta += a
        td += d
        rate = f"{a/(a+d)*100:3.0f}%" if (a + d) else "  - "
        print(f"{key:14} {a:6d} {d:7d} {u:8d}   {rate}")
    if ta + td:
        print(f"{'OVERALL':14} {ta:6d} {td:7d} {'':>8}   {ta/(ta+td)*100:3.0f}%")

    for key in sorted(examples):
        rows = examples[key][: args.show]
        print(f"\n--- where you and the bot differed: {key} ({len(examples[key])}) ---")
        for n, row in enumerate(rows):
            extra = repeats[key].get(n, 0)
            print(row + (f"   (bot held this {extra + 1}x)" if extra else ""))
        if len(examples[key]) > len(rows):
            print(f"    ... and {len(examples[key]) - len(rows)} more")

    print("\nNote: 'unclear' means the human's choice could not be reconstructed")
    print("with confidence from state diffs -- not that you and the bot agreed.")


if __name__ == "__main__":
    main()
