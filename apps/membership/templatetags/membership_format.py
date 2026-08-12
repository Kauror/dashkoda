"""Display filters for the Liikmeskond page.

Thin wrappers over `apps.core.formatting`, which is where the shape of a euro
amount and a percentage is actually decided. They exist so a template can ask
for that shape without a view having to pre-format every field of an
observation into a parallel set of context variables.
"""

from __future__ import annotations

from django import template

from apps.core.formatting import group_thousands, integer, percentage, whole_euros

register = template.Library()


@register.filter(name="group_thousands")
def group_thousands_filter(value):
    """A whole count, grouped so four digits are read rather than counted.

    The card printed its member totals raw while the euro amounts beside them
    were grouped, so one figure in the same row read as `3402` and another as
    `1 276 101 €`.

    Takes a number. Chaining it after `floatformat` hands it a *string*, which
    is a `ValueError` rather than a wrong figure — see `integer` below, which is
    what a `Decimal` count wants.
    """
    if value is None:
        return ""
    return group_thousands(value)


@register.filter(name="integer")
def integer_filter(value):
    """A `Decimal` count as a whole grouped number: `1 276`.

    `group_thousands` alone renders `Decimal("4.00")` as `4.00`, because it
    groups whatever it is given rather than quantising it. Reaching for
    `floatformat:0` first to fix that is what broke the E-pood pages: the filter
    returns a string, and formatting a string with `,` raises.
    """
    if value is None:
        return ""
    return integer(value)


@register.filter(name="whole_euros")
def whole_euros_filter(value):
    """A euro amount to the nearest whole euro, thousands grouped.

    `None` is passed straight through rather than becoming an empty string, so
    the partial still distinguishes "not reported" from a blank.
    """
    if value is None:
        return None
    return whole_euros(value)


@register.filter(name="percentage")
def percentage_filter(value):
    """A percentage at two decimals, so a stored four does not read as precision."""
    return percentage(value)
