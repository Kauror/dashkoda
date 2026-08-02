"""How a number looks to a reader, decided once for the whole dashboard.

Two figures that mean the same thing must not be written two different ways on
two pages. The membership fee appears on the overview card and again on the
Liikmeskond page, and before this module existed the first was grouped whole
euros and the second an ungrouped `1276101,00` — the same amount, read as two.

Formatting only. Nothing here rounds a value that is then stored, compared or
charted: the caller keeps the exact figure and asks for a display string when it
is about to write one.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Estonian groups thousands with a non-breaking space, as Django's own `et`
# locale does. Written as an escape because the character is invisible in
# source, and it must be non-breaking so a grouped figure never wraps in the
# middle of itself.
GROUP_SEPARATOR = "\N{NO-BREAK SPACE}"


def group_thousands(value: Decimal | int) -> str:
    """A whole number, grouped so it can be read at a glance.

    `1276101` has to be counted digit by digit; `1 276 101` does not.
    """
    return f"{value:,}".replace(",", GROUP_SEPARATOR)


def whole_euros(amount: Decimal | int | None) -> str:
    """A euro amount to the nearest whole euro, grouped.

    Cents are noise beside a budget in the millions, and `1276101,00` spends two
    digits of precision on the part nobody reads.
    """
    if amount is None:
        return ""
    whole = Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return group_thousands(whole)


def percentage(value: Decimal | int | None, *, places: int = 2) -> Decimal | None:
    """A percentage at two decimals.

    The board report stores four, and `94,0400 %` reads as a precision the
    figure does not have. Returned as a `Decimal` rather than a string so the
    template's own locale still renders the decimal comma.
    """
    if value is None:
        return None
    exponent = Decimal(1).scaleb(-places)
    return Decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)
