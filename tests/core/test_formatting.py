"""How a number and a date look to a reader.

Pure functions, no database and no Django settings. The point of these is that
one figure has one written form: the same amount must not be grouped on one page
and ungrouped on another, and the short date a template writes with `j.m.y` must
be the same short date a Python-built string writes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from apps.core.formatting import (
    GROUP_SEPARATOR,
    group_thousands,
    percentage,
    short_date,
    whole_euros,
)


def test_a_grouped_amount_can_be_read_without_counting_digits():
    assert group_thousands(1276101) == f"1{GROUP_SEPARATOR}276{GROUP_SEPARATOR}101"


def test_cents_are_dropped_from_an_amount_nobody_reads_them_in():
    assert whole_euros(Decimal("1276101.49")) == f"1{GROUP_SEPARATOR}276{GROUP_SEPARATOR}101"
    assert whole_euros(None) == ""


def test_a_percentage_is_shown_at_the_precision_it_has():
    assert percentage(Decimal("94.0400")) == Decimal("94.04")
    assert percentage(None) is None


def test_the_short_date_is_the_one_the_templates_write():
    """`j.m.y`: the day unpadded, the month padded, the year in two digits.

    Written out in Python because a string built on the server — an SVG tooltip,
    a chart summary — cannot go through a template filter, and two definitions
    of the short date would drift apart the moment one of them changed.
    """
    assert short_date(dt.date(2026, 6, 4)) == "4.06.26"
    assert short_date(dt.date(2026, 11, 15)) == "15.11.26"
    assert short_date(dt.date(2011, 1, 1)) == "1.01.11"


def test_the_short_date_takes_a_timestamp_and_states_no_time():
    """A date names a day. The two timestamps that name an *action* keep their
    clock time, and they do not go through this."""
    assert short_date(dt.datetime(2026, 6, 4, 13, 58, 19)) == "4.06.26"


def test_an_absent_date_is_an_empty_string_not_a_guess():
    assert short_date(None) == ""
