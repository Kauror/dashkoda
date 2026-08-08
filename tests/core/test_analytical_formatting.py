"""How the analytical readouts write a number, pinned once.

`test_formatting.py` covers the display helpers the cards already used. This
module covers the vocabulary the Liikmeskond charts add: signed changes,
percentage points, euro amounts with their symbol, and Estonian dates and month
names. A tooltip, an aria label and an ECharts payload all state the same figure
and none of them can go through a template filter, so these are the one place
the spelling is decided.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.core.formatting import (
    GROUP_SEPARATOR,
    MINUS_SIGN,
    MONTH_NAMES,
    day_and_month,
    euros,
    integer,
    long_date,
    month_and_year,
    month_name,
    percent,
    percentage_points,
    signed_integer,
    signed_percent,
)

NBSP = GROUP_SEPARATOR


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3412, f"3{NBSP}412"), (0, "0"), (-1234, f"{MINUS_SIGN}1{NBSP}234"), (None, "")],
)
def test_integers_are_grouped_and_use_a_real_minus(value, expected):
    assert integer(value) == expected


def test_a_euro_amount_carries_its_symbol_without_breaking():
    """The amount and the symbol are joined by the same non-breaking space that
    groups the thousands, so `742 400 €` can never wrap in the middle."""
    assert euros(742400) == f"742{NBSP}400{NBSP}€"
    assert NBSP in euros(742400)


def test_cents_are_rounded_away_rather_than_shown():
    assert euros(Decimal("1276101.49")) == f"1{NBSP}276{NBSP}101{NBSP}€"


def test_a_missing_amount_is_blank_and_never_a_zero():
    assert euros(None) == ""
    assert integer(None) == ""
    assert percent(None) == ""
    assert signed_integer(None) == ""
    assert percentage_points(None) == ""


def test_percentages_use_the_estonian_decimal_comma():
    assert percent(Decimal("72.8333")) == "72,8%"


def test_a_percentage_is_stated_to_one_decimal_by_default():
    """The board report stores four. A collection figure read once a month does
    not have four decimals of precision, and printing them claims it does."""
    assert percent(Decimal("94.0400")) == "94,0%"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(27, "+27"), (-17, f"{MINUS_SIGN}17"), (0, "0")],
)
def test_a_signed_integer_signs_gains_and_losses_but_not_zero(value, expected):
    """`+0` reads as a gain that rounded down; here "no change" is a real
    answer and is written as one."""
    assert signed_integer(value) == expected


def test_a_signed_percentage_follows_the_same_rule():
    assert signed_percent(Decimal("14.32")) == "+14,3%"
    assert signed_percent(Decimal("-3.84")) == f"{MINUS_SIGN}3,8%"


def test_percentage_points_name_their_unit():
    """A share that rose 3,4 points did not rise 3,4 percent. Both statements
    are true of the same movement and they are different numbers."""
    assert percentage_points(Decimal("3.4")) == f"+3,4{NBSP}pp"
    assert percentage_points(Decimal("-1.2")) == f"{MINUS_SIGN}1,2{NBSP}pp"


def test_rounding_is_symmetric_about_zero():
    """Half-away-from-zero, the same rule `percentage()` already used, so a
    gain and an equal loss are never rounded different distances."""
    assert percentage_points(Decimal("1.25")) == f"+1,3{NBSP}pp"
    assert percentage_points(Decimal("-1.25")) == f"{MINUS_SIGN}1,3{NBSP}pp"


def test_the_minus_is_a_minus_sign_and_not_a_hyphen():
    """It is the width of a plus, so a column of signed figures stays aligned,
    and it cannot be read as the hyphen in a range."""
    assert signed_integer(-17) != "-17"
    assert MINUS_SIGN == "\N{MINUS SIGN}"


def test_dates_are_written_the_way_estonian_writes_them():
    assert long_date(dt.date(2026, 7, 31)) == "31.07.2026"
    assert long_date(dt.date(2026, 1, 5)) == "05.01.2026"


def test_a_long_date_keeps_the_century_because_a_tooltip_is_the_only_place_to_check():
    assert long_date(dt.date(2026, 7, 31)).endswith("2026")


def test_day_and_month_and_month_and_year_read_as_estonian():
    assert day_and_month(dt.date(2026, 7, 31)) == "31. juuli"
    assert month_and_year(dt.date(2026, 7, 1)) == "juuli 2026"


def test_no_visible_date_is_ever_iso():
    for rendered in (
        long_date(dt.date(2026, 7, 31)),
        day_and_month(dt.date(2026, 7, 31)),
        month_and_year(dt.date(2026, 7, 31)),
    ):
        assert "2026-07-31" not in rendered


def test_month_abbreviations_replace_the_roman_numerals():
    """The board reports number months I–XII. The axis does not, because a
    reader should not have to translate before they can read a chart."""
    assert month_name(2, short=True) == "veebr"
    assert month_name(12, short=True) == "dets"
    assert month_name(7) == "juuli"


def test_the_manual_entry_form_labels_still_come_from_one_vocabulary():
    """`forms.MONTHS` and the chart axis must not drift into two spellings."""
    from apps.membership.forms import MONTHS

    assert tuple(label for _, label in MONTHS) == tuple(name.capitalize() for name in MONTH_NAMES)
