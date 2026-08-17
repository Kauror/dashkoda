"""Every focus view actually renders, checked without a database.

This exists because of a specific and repeated failure mode in this repository:
a template that compiles, a view that returns, and a section that silently
renders nothing because a context key moved. Django resolves a missing variable
to falsy rather than raising, so a renamed field removes a whole section without
any check noticing.

Compiling a template proves it parses. Only rendering it proves the names in it
resolve, so these tests build the presenter objects directly and render the real
templates — which needs no PostgreSQL and therefore runs everywhere.

The assertions are about what must never reach a reader: an inline style the
Content Security Policy forbids, an untranslated placeholder, and the specific
words this domain refuses to say.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.template.loader import render_to_string

from apps.shop.models import ProductType
from apps.shop.page import (
    ProductDetail,
    ShopOverview,
    _focus_options,
    _metric_options,
)
from apps.shop.periods import FOCUSES, METRIC_UNITS, resolve_period
from apps.shop.selectors import ComparisonWindow
from apps.shop.vocabulary import vocabulary_for

ANCHOR = dt.date(2026, 8, 11)

#: Words this dashboard must never print, and what each would misrepresent.
#:
#: Matched **case-insensitively**, which is what catches the real failure mode:
#: `laekunud tulu` first reached the page inside a negation — "pangalingiga
#: tellitud väärtus ei ole laekunud tulu" — a true sentence that nonetheless
#: prints the forbidden label where a skimming reader may take it for one.
#:
#: The list holds only terms with **no legitimate use in prose**. Refunds,
#: downloads and attendance are deliberately absent from it: the module is
#: required to *say* that it has no such figure, and "Koda.ee ei salvesta
#: laekumist ega tagasimakseid" is that disclosure rather than a violation of
#: it. The rule forbids the metric, not the sentence explaining its absence —
#: so those three are guarded by asserting no such KPI exists, not by banning
#: the word.
FORBIDDEN = {
    "laekunud tulu": "Koda.ee records no payment receipt",
    "müügitulu": "ordered value is not sales revenue",
    "käive": "ordered value is not turnover",
    "osalejad": "a registration is not an attendee",
    "osavõtjad": "a registration is not an attendee",
    "konversioonimäär": "page views are not visitors, so this is not a conversion rate",
}


def _forbidden_in(html: str) -> list[str]:
    lowered = html.casefold()
    return [f"{word} ({why})" for word, why in FORBIDDEN.items() if word in lowered]


def _state():
    return {
        "product_type": "",
        "categories": (),
        "search": "",
        "sort": "",
        "member_status": "",
    }


def _overview(focus) -> ShopOverview:
    resolved = resolve_period("1a", None, None, anchor=ANCHOR)
    window = ComparisonWindow(dt.date(2025, 8, 12), ANCHOR, dt.date(2025, 8, 12), ANCHOR)
    return ShopOverview(
        has_source=True,
        as_of_label="11.08.2026",
        coverage_label="22.10.2020–11.08.2026",
        window=window,
        web_interval_label="16.06.2023–11.08.2026",
        web_is_partial=True,
        period=resolved,
        focus=focus.key,
        focus_label=focus.label,
        focus_options=_focus_options(focus.key, resolved, _state(), METRIC_UNITS),
        focus_links={item.key: f"?fookus={item.key}" for item in FOCUSES},
        trend_options=_metric_options(METRIC_UNITS, resolved, _state(), focus.key, "Tellimusridu"),
    )


def _render(template: str, context: dict) -> str:
    return render_to_string(
        template,
        {"navigation": [], "active_nav": "shop", "freshness": None, **context},
    )


@pytest.mark.parametrize("focus", FOCUSES, ids=[focus.key for focus in FOCUSES])
def test_every_focus_view_renders(focus):
    html = _render("shop/overview.html", {"overview": _overview(focus)})

    # The label appears in the navigation; the per-focus question sentence
    # left the page with the heading block that rendered it (2026-08-16).
    assert focus.label in html


@pytest.mark.parametrize("focus", FOCUSES, ids=[focus.key for focus in FOCUSES])
def test_no_focus_view_emits_an_inline_style(focus):
    """The CSP is `style-src 'self'`; a proportion may only be geometry."""
    html = _render("shop/overview.html", {"overview": _overview(focus)})

    assert 'style="' not in html


@pytest.mark.parametrize("focus", FOCUSES, ids=[focus.key for focus in FOCUSES])
def test_no_focus_view_uses_a_forbidden_word(focus):
    html = _render("shop/overview.html", {"overview": _overview(focus)})

    assert not _forbidden_in(html), f"{focus.key} printed: {_forbidden_in(html)}"


def test_the_focus_navigation_marks_the_current_view_for_a_screen_reader():
    html = _render("shop/overview.html", {"overview": _overview(FOCUSES[2])})

    assert 'aria-current="page"' in html


def test_every_focus_is_reachable_from_every_other_one():
    html = _render("shop/overview.html", {"overview": _overview(FOCUSES[0])})

    for focus in FOCUSES[1:]:
        assert f"fookus={focus.key}" in html, f"{focus.key} was unreachable"


@pytest.mark.parametrize("focus", FOCUSES, ids=[focus.key for focus in FOCUSES])
def test_the_as_of_date_no_longer_appears_on_any_focus(focus):
    """`Andmete kohta`, and the as-of date inside it, left this page whole.

    Both moved off the dashboard to `/haldus/` on 2026-08-17 — see
    `apps/shop/templates/shop/admin/_data_about.html`, which is where
    `test_admin_area.py` now asserts the date arrived.
    """
    html = _render("shop/overview.html", {"overview": _overview(focus)})

    assert "11.08.2026" not in html
    assert "Andmete kohta" not in html


def test_a_page_without_a_source_still_renders():
    overview = ShopOverview(
        has_source=False,
        as_of_label="",
        coverage_label="",
        window=ComparisonWindow(None, None, None, None),
        web_interval_label="",
        web_is_partial=False,
        focus="ulevaade",
        focus_label="Ülevaade",
    )

    html = _render("shop/overview.html", {"overview": overview})

    assert "ei ole veel imporditud" in html


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------


def _detail(product_type: str) -> ProductDetail:
    words = vocabulary_for(product_type)
    is_event = product_type == ProductType.EVENT_REGISTRATION
    return ProductDetail(
        found=True,
        as_of_label="11.08.2026",
        coverage_label="22.10.2020–11.08.2026",
        web_interval_label="16.06.2023–11.08.2026",
        web_is_partial=True,
        title="Näidisleping",
        type_label=ProductType(product_type).label,
        period=resolve_period("1a", None, None, anchor=ANCHOR),
        units_label=words.units_label,
        units_noun=words.units_noun,
        views_label=words.views_label,
        rate_label=words.rate_label,
        denominator_label=words.views_label,
        denominator_views="1 240",
        event_url="https://www.koda.ee/et/sundmused/x" if is_event else "",
        product_url="" if is_event else "https://www.koda.ee/et/pood/x",
    )


@pytest.mark.parametrize("product_type", ProductType.values)
def test_the_product_page_renders_for_every_family(product_type):
    html = _render("shop/product.html", {"detail": _detail(product_type)})

    assert 'style="' not in html
    assert not _forbidden_in(html), f"{product_type} printed: {_forbidden_in(html)}"


def test_the_product_page_names_the_page_its_rate_divides_by():
    """The heading and the rate must describe the same page."""
    event = _render("shop/product.html", {"detail": _detail(ProductType.EVENT_REGISTRATION)})
    document = _render("shop/product.html", {"detail": _detail(ProductType.DOCUMENT)})

    assert "Sündmuse lehe vaatamised" in event
    assert "Registreerimisi / 100 vaatamist" in event
    assert "Tootelehe vaatamised" in document
    assert "Oste / 100 vaatamist" in document


def test_an_event_page_says_a_registration_is_not_an_attendee():
    html = _render("shop/product.html", {"detail": _detail(ProductType.EVENT_REGISTRATION)})

    assert "ei ole osaleja" in html
