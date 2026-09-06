import pathlib

p = pathlib.Path(r"C:\Claude STS Bot\sts2_bot\bot\strategy\combat.py")
s = p.read_text(encoding="utf-8")

HELPER = '''def _defence_already_covered(
    card: dict[str, Any], enemies: list[dict[str, Any]], unblocked: int
) -> bool:
    """A Block card whose Block we do not need and whose rider is worth ~nothing.

    `_is_pure_block` only catches cards that do *literally* nothing else, so
    Leg Sweep+ ("Apply 3 Weak. Gain 18 Block") sailed through the value step
    and got played on a turn already covered against a 5-damage attack --
    where its Weak prevents 1. Observed live: 37 Block banked against 5
    incoming while three Strikes sat unplayed and a 172 HP enemy took 1 damage.

    Only fires when the incoming hit is *already* fully blocked and the card's
    mitigation is below the bar the mitigation step itself uses, so a needed
    Block or a meaningful Weak is untouched.
    """
    if unblocked > 0:
        return False
    if _effective_block(card, 0) <= 0:
        return False
    if _card_damage(card) > 0 or _immediate_poison(card) > 0 or _card_shiv_count(card) > 0:
        return False
    if _DRAWS_CARDS_RE.search(card.get("description", "") or ""):
        return False  # drawing is real value regardless of the board
    if _is_power(card):
        return False
    return _mitigation_value(card, enemies) < MIN_MITIGATION_VALUE


def _is_pure_block('''

old = "def _is_pure_block("
assert old in s, "is_pure_block missing"
s = s.replace(old, HELPER, 1)

OLD_FILTER = "        and (must_block or not _is_pure_block(c))"
NEW_FILTER = ("        and (must_block or not _is_pure_block(c))\n"
              "        and not _defence_already_covered(c, threatening, unblocked)")
assert OLD_FILTER in s, "value filter missing"
s = s.replace(OLD_FILTER, NEW_FILTER, 1)

p.write_text(s, encoding="utf-8")
print("applied")
