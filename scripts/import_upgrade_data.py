"""Import per-card upgrade data from the running game.

The card database was built from community pick-rate stats, which say nothing
about upgrades -- so the bot had no way to know that some cards roughly double
when upgraded while others barely move. Any "which card should I upgrade?"
decision was therefore guesswork.

The mod's wiki endpoint exposes both variants of every *discovered* card:

    "base":     {"cost": "1", "description": "Apply 5 Poison."}
    "upgraded": {"cost": "1", "description": "Apply 7 Poison."}

This script walks the Silent card list, pulls both variants, and writes
`bot/strategy/data/silent_upgrades.json` with the raw text plus a derived
`gain` score. Re-run it after a game update, or when more cards have been
discovered (the endpoint only sees the active profile's discoveries).

Usage (game must be running with mods enabled):
    python scripts/import_upgrade_data.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

API = "http://localhost:15526/api/v1/wiki"
DATA_DIR = Path(__file__).resolve().parent.parent / "bot" / "strategy" / "data"
CARDS_FILE = DATA_DIR / "silent_cards.json"
OUT_FILE = DATA_DIR / "silent_upgrades.json"

_NUM_AFTER = {
    "damage": re.compile(r"[Dd]eal (\d+) damage"),
    "block": re.compile(r"[Gg]ain (\d+) Block"),
    "poison": re.compile(r"[Aa]pply (\d+) Poison"),
    "draw": re.compile(r"[Dd]raw (\d+) card"),
    "shivs": re.compile(r"[Aa]dd (\d+)[^.]*Shiv"),
    "weak": re.compile(r"[Aa]pply (\d+) Weak"),
    "vulnerable": re.compile(r"[Aa]pply (\d+) Vulnerable"),
    "strength": re.compile(r"[Gg]ain (\d+) Strength"),
    "dexterity": re.compile(r"[Gg]ain (\d+) Dexterity"),
}

# Weights turn raw stat deltas into a single comparable "how much better is
# this card upgraded?" number. Energy is the scarcest resource in the game, so
# a cost reduction is worth far more than a couple of points of damage.
COST_REDUCTION_WEIGHT = 25.0
_STAT_WEIGHT = {
    "damage": 1.0,
    "block": 1.0,
    "poison": 2.0,   # poison ignores block and compounds
    "draw": 8.0,     # card advantage
    "shivs": 4.0,
    "weak": 5.0,
    "vulnerable": 5.0,
    "strength": 8.0,
    "dexterity": 6.0,
}
# "Exhaust" disappearing, or Retain/Innate appearing, are real upgrades that
# no number captures.
_KEYWORD_GAINS = (("exhaust", -1), ("retain", 1), ("innate", 1), ("sly", 1))
KEYWORD_WEIGHT = 6.0
# Catch-all weight for numeric upgrades no specific pattern names.
GENERIC_NUMBER_WEIGHT = 2.0


def _cost_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None  # "X" cost


def _stats(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, pattern in _NUM_AFTER.items():
        m = pattern.search(text or "")
        if m:
            out[key] = int(m.group(1))
    return out


def _energy_icons(text: str) -> int:
    """Energy is rendered as repeated icon tags, so "gain 2 energy" has no
    digits at all -- Adrenaline and Tactician both scored 0 until this."""
    return (text or "").count("energy_icon")


def _generic_numeric_gain(base_text: str, up_text: str) -> float:
    """Total positive delta across every number in the text, positionally paired.

    A catch-all for the many upgrades no specific pattern covers: "3 times" ->
    "4 times", "Retain up to 1" -> "2", "Gain 4 Thorns" -> "6", "until you have
    6 in your Hand" -> "7". Enumerating each phrasing is a losing game; this
    only requires that the sentence shape stays the same, which upgrades
    almost always do.
    """
    base_nums = [int(n) for n in re.findall(r"\d+", base_text or "")]
    up_nums = [int(n) for n in re.findall(r"\d+", up_text or "")]
    if len(base_nums) != len(up_nums):
        return 0.0  # shape changed -- positional pairing would be nonsense
    return float(sum(u - b for b, u in zip(base_nums, up_nums) if u > b))


def upgrade_gain(base: dict, upgraded: dict) -> float:
    """How much better the upgraded version is. Higher = upgrade this first."""
    base_text = base.get("description") or ""
    up_text = upgraded.get("description") or ""

    # Known stat patterns get accurate weights; the generic numeric diff
    # catches everything else. Take whichever found more -- they measure the
    # same thing, so summing would double-count.
    specific = 0.0
    base_stats, up_stats = _stats(base_text), _stats(up_text)
    for key, weight in _STAT_WEIGHT.items():
        delta = up_stats.get(key, 0) - base_stats.get(key, 0)
        if delta > 0:
            specific += delta * weight
    gain = max(specific, _generic_numeric_gain(base_text, up_text) * GENERIC_NUMBER_WEIGHT)

    # Extra energy is worth as much as a cost reduction -- same resource.
    energy_delta = _energy_icons(up_text) - _energy_icons(base_text)
    if energy_delta > 0:
        gain += energy_delta * COST_REDUCTION_WEIGHT

    base_cost, up_cost = _cost_int(base.get("cost")), _cost_int(upgraded.get("cost"))
    if base_cost is not None and up_cost is not None and up_cost < base_cost:
        gain += (base_cost - up_cost) * COST_REDUCTION_WEIGHT

    low_base, low_up = base_text.lower(), up_text.lower()
    for word, direction in _KEYWORD_GAINS:
        had, has = word in low_base, word in low_up
        if direction < 0 and had and not has:
            gain += KEYWORD_WEIGHT  # lost a drawback
        elif direction > 0 and has and not had:
            gain += KEYWORD_WEIGHT  # gained an upside

    # "Shiv" -> "Shiv+", "play" -> "Upgrade and play": the upgrade improves
    # what the card generates rather than its own numbers.
    if "+" in up_text and "+" not in base_text:
        gain += KEYWORD_WEIGHT
    if "upgrade" in low_up and "upgrade" not in low_base:
        gain += KEYWORD_WEIGHT

    return round(gain, 1)


def fetch(name: str) -> dict | None:
    url = f"{API}?query={urllib.parse.quote(name)}&item_type=card&limit=5"
    try:
        data = requests.get(url, timeout=10).json()
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"  ! {name}: request failed ({exc})")
        return None
    for result in data.get("results", []):
        if (result.get("name") or "").strip().lower() == name.strip().lower():
            return result
    return None


def main() -> None:
    cards = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
    names = [c["name"] for c in cards["cards"]] + [c["name"] for c in cards["starters"]]

    out: dict[str, dict] = {}
    missing: list[str] = []
    for name in names:
        result = fetch(name)
        if not result or not result.get("upgraded"):
            missing.append(name)
            continue
        base, upgraded = result.get("base") or {}, result.get("upgraded") or {}
        out[name] = {
            "base_cost": base.get("cost"),
            "upgraded_cost": upgraded.get("cost"),
            "base_text": base.get("description"),
            "upgraded_text": upgraded.get("description"),
            "gain": upgrade_gain(base, upgraded),
        }
        time.sleep(0.05)  # be gentle with the local server

    payload = {
        "_source": "STS2MCP /api/v1/wiki (base + upgraded variants), imported live",
        "_note": (
            "gain = weighted delta between base and upgraded. Cost reductions "
            "dominate; poison/draw/debuffs weigh more than raw damage. Only "
            "cards discovered by the active profile are available."
        ),
        "cards": out,
    }
    OUT_FILE.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    print(f"imported {len(out)} cards -> {OUT_FILE}")
    if missing:
        print(f"missing ({len(missing)}): {', '.join(missing[:20])}"
              + (" ..." if len(missing) > 20 else ""))
    ranked = sorted(out.items(), key=lambda kv: kv[1]["gain"], reverse=True)
    print("\nbiggest upgrade gains:")
    for name, info in ranked[:12]:
        print(f"  {info['gain']:>6}  {name:22} {info['base_text']} -> {info['upgraded_text']}")


if __name__ == "__main__":
    sys.exit(main())
