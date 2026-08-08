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

from datetime import date, datetime
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


def short_date(day: date | datetime | None) -> str:
    """A date as the viewer-facing templates write one: `4.06.26`.

    The same form as Django's `j.m.y`, which every viewer template now uses.
    Written out here because a string built in Python — an SVG tooltip, an aria
    label, a chart summary — cannot go through a template filter, and two
    definitions of "the short date" would drift the moment one of them changed.
    """
    if day is None:
        return ""
    if isinstance(day, datetime):
        day = day.date()
    return f"{day.day}.{day.month:02d}.{day.year % 100:02d}"


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


# --------------------------------------------------------------------------
# Analytical display vocabulary
#
# The charts state numbers in three places that cannot share a template filter:
# a tooltip built in Python, an ECharts payload read by the browser, and an aria
# label. Each format is named once here so those three cannot write the same
# figure three ways.
# --------------------------------------------------------------------------

# Estonian writes a decimal comma. A figure assembled in Python bypasses the
# template's locale, so the comma has to be put back deliberately.
DECIMAL_SEPARATOR = ","

# U+2212 MINUS SIGN, not the hyphen a keyboard produces. It is the width of a
# plus, so a column of signed figures stays aligned, and it cannot be read as
# the hyphen in a range or a compound word. `apps.core.text_folding` folds it
# back to a hyphen when it has to match against source text; nothing here is
# ever matched against source text.
MINUS_SIGN = "\N{MINUS SIGN}"

MONTH_NAMES: tuple[str, ...] = (
    "jaanuar",
    "veebruar",
    "märts",
    "aprill",
    "mai",
    "juuni",
    "juuli",
    "august",
    "september",
    "oktoober",
    "november",
    "detsember",
)

# What a month axis is labelled with. Full names do not fit twelve to a line on
# a phone, and the board reports' Roman numerals make a reader translate before
# they can read. These are the ordinary written abbreviations.
MONTH_ABBREVIATIONS: tuple[str, ...] = (
    "jaan",
    "veebr",
    "märts",
    "apr",
    "mai",
    "juuni",
    "juuli",
    "aug",
    "sept",
    "okt",
    "nov",
    "dets",
)


def month_name(month: int, *, short: bool = False) -> str:
    """One month, in Estonian, lowercase as the language writes it mid-sentence."""
    names = MONTH_ABBREVIATIONS if short else MONTH_NAMES
    return names[month - 1]


def long_date(day: date | datetime | None) -> str:
    """A date in full: `31.07.2026`.

    The short form drops the century, which is right in a card footer and wrong
    in a tooltip that is the only place a reader can check which year a point
    belongs to.
    """
    if day is None:
        return ""
    if isinstance(day, datetime):
        day = day.date()
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def day_and_month(day: date | datetime | None) -> str:
    """`31. juuli` — for an axis where the year is already established."""
    if day is None:
        return ""
    if isinstance(day, datetime):
        day = day.date()
    return f"{day.day}. {month_name(day.month)}"


def month_and_year(day: date | datetime | None) -> str:
    """`juuli 2026` — for a monthly series, where the day means nothing."""
    if day is None:
        return ""
    if isinstance(day, datetime):
        day = day.date()
    return f"{month_name(day.month)} {day.year}"


def _decimals(value: Decimal | int | float, places: int) -> str:
    """A number at fixed decimals, grouped, with the Estonian decimal comma."""
    quantised = Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{abs(quantised):.{places}f}".partition(".")
    grouped = group_thousands(int(whole))
    body = f"{grouped}{DECIMAL_SEPARATOR}{fraction}" if places else grouped
    return f"{MINUS_SIGN}{body}" if quantised < 0 else body


def integer(value: Decimal | int | None) -> str:
    """`3 412`."""
    if value is None:
        return ""
    return _decimals(value, 0)


def euros(amount: Decimal | int | None) -> str:
    """`742 400 €`.

    The symbol is joined with the same non-breaking space that groups the
    thousands, so the amount and its unit never break across a line.
    """
    if amount is None:
        return ""
    return f"{whole_euros(amount)}{GROUP_SEPARATOR}€"


def percent(value: Decimal | int | None, *, places: int = 1) -> str:
    """`72,8%`.

    One decimal by default. The board report stores four, and a completion
    figure quoted to four decimals claims a precision that a sum of invoices
    read once a month does not have.
    """
    if value is None:
        return ""
    return f"{_decimals(value, places)}%"


def _signed(body: str, *, negative: bool, zero: bool) -> str:
    if zero:
        return body
    return body if negative else f"+{body}"


def signed_integer(value: Decimal | int | None) -> str:
    """`+27`, `−17`, `0`.

    Zero carries no sign: `+0` reads as a rounded-down gain, and this is used
    where "no change" is a real answer.
    """
    if value is None:
        return ""
    return _signed(_decimals(value, 0), negative=value < 0, zero=value == 0)


def signed_percent(value: Decimal | int | None, *, places: int = 1) -> str:
    """`+14,3%`, `−3,8%`, `0%` — a change *in* percent, not a share."""
    if value is None:
        return ""
    return _signed(f"{_decimals(value, places)}%", negative=value < 0, zero=value == 0)


def percentage_points(value: Decimal | int | None, *, places: int = 1) -> str:
    """`+3,4 pp`.

    A share that moved from 92,6% to 96,0% did not rise by 3,4% — it rose by
    3,4 percentage points, and the two are different numbers. The unit is
    spelled out because the distinction is the whole reason this exists.
    """
    if value is None:
        return ""
    body = _signed(_decimals(value, places), negative=value < 0, zero=value == 0)
    return f"{body}{GROUP_SEPARATOR}pp"
