"""What each product family's numbers are called.

The shop sells three quite different things through one set of tables, and the
generic word for a row in `ShopDailyFact.units` is **`Soetatud`** — acquired.
That is not a stylistic preference over `Ostetud`:

- a large share of the contract templates are **free**, so "bought" is wrong for
  the acquisition it describes and `Tasuta` + `Ostetud` in the same sentence
  reads as a contradiction;
- an event registration is not a purchase of an object at all;
- only a physical product is unambiguously *bought*, and there `Ostetud ühikud`
  stays available and is used.

So `Soetatud` is the word that is true for every row, and each family may narrow
it where narrowing is more informative than the general term.

## What is deliberately not said

`Registreerimised` is offered for event products because the Commerce item *is*
a registration line. **`Osalejad` is not**, and no wording here implies it: the
shop dataset records that a registration was completed, never that anybody
attended. Attendance is not a fact this application has, and the distinction is
carried in the interface rather than left to a reader's assumption.

The view labels are derived from `web_effectiveness.denominator_role` rather
than restated, so a family whose acquisition page changes cannot end up with a
heading naming one page and a rate dividing by another.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import PageRole, ProductType
from .web_effectiveness import denominator_role

#: What each page role is called when its views are shown as a denominator.
_ROLE_VIEW_LABELS: dict[str, str] = {
    PageRole.PRODUCT: "Tootelehe vaatamised",
    PageRole.EVENT: "Sündmuse lehe vaatamised",
    PageRole.INFORMATION: "Tutvustuse vaatamised",
}

#: The heading for a mixed population. `Ostuleht` covers a product page and an
#: event page at once without naming either, which is what a reader looking at
#: all three families needs.
_MIXED_VIEW_LABEL = "Ostulehe vaatamised"


@dataclass(frozen=True)
class Vocabulary:
    """How one selected product family's figures are worded."""

    #: The headline count: `Soetatud`, or a family's own narrower word.
    units_label: str
    #: The noun that follows a bare count — "1 245 ühikut".
    units_noun: str
    #: The web denominator's heading, derived from the denominator role.
    views_label: str
    #: The rate, spelled out. Always per hundred views, never a percentage.
    rate_label: str
    #: The same rate as a table column heading, where four words will not fit.
    rate_column: str
    #: What the trend's own series is called in a chart legend.
    trend_label: str

    @property
    def units_column(self) -> str:
        """The ranking column heading — one word, because the column is narrow."""
        return self.units_label


_ALL = Vocabulary(
    units_label="Soetatud",
    units_noun="ühikut",
    views_label=_MIXED_VIEW_LABEL,
    rate_label="Soetamisi / 100 vaatamist",
    rate_column="/ 100",
    trend_label="Soetatud",
)

_BY_TYPE: dict[str, Vocabulary] = {
    ProductType.DOCUMENT: Vocabulary(
        units_label="Soetatud",
        units_noun="näidist",
        views_label=_ROLE_VIEW_LABELS[denominator_role(ProductType.DOCUMENT)],
        rate_label="Soetamisi / 100 vaatamist",
        rate_column="/ 100",
        trend_label="Soetatud lepingunäidised",
    ),
    ProductType.EVENT_REGISTRATION: Vocabulary(
        units_label="Registreerimised",
        units_noun="registreerimist",
        views_label=_ROLE_VIEW_LABELS[denominator_role(ProductType.EVENT_REGISTRATION)],
        rate_label="Registreerimisi / 100 vaatamist",
        rate_column="/ 100",
        trend_label="Registreerimised",
    ),
    ProductType.PHYSICAL_PRODUCT: Vocabulary(
        units_label="Ostetud ühikud",
        units_noun="ühikut",
        views_label=_ROLE_VIEW_LABELS[denominator_role(ProductType.PHYSICAL_PRODUCT)],
        rate_label="Soetamisi / 100 vaatamist",
        rate_column="/ 100",
        trend_label="Ostetud ühikud",
    ),
}


def vocabulary_for(product_type: str) -> Vocabulary:
    """The wording for one selected type, or the generic wording for all of them.

    An unrecognised type resolves to the generic vocabulary rather than raising:
    a rotted bookmark must render a page, and `Soetatud` is true of every row
    whatever family it came from.
    """
    return _BY_TYPE.get(product_type, _ALL)


__all__ = ["Vocabulary", "vocabulary_for"]
