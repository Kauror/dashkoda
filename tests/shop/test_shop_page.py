"""The E-pood pages: what they disclose, and what they refuse to claim."""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.shop.importing import import_shop_package

from .package_factory import (
    DOCUMENT_PRODUCT_PAGE_ONLY,
    DOCUMENT_WITH_BOTH_PAGES,
    build_package,
    default_manifest,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(tmp_path):
    import_shop_package(build_package(tmp_path), dry_run=False)


def _get(client, authenticate_viewer, url="/epood/"):
    authenticate_viewer(client)
    return client.get(url)


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_no_source_says_so_rather_than_showing_zeros(client, authenticate_viewer):
    content = _get(client, authenticate_viewer).content.decode()

    assert "ei ole veel imporditud" in content
    assert "0 €" not in content


def test_the_page_is_behind_the_viewer_gate(client):
    response = client.get("/epood/")
    assert response.status_code in (302, 403)


# ---------------------------------------------------------------------------
# Source disclosure
# ---------------------------------------------------------------------------


def test_the_as_of_date_is_shown(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    assert "E-poe andmed seisuga 11.08.2026" in content
    assert "Tellimuste ajalugu" in content


def test_the_dataset_is_never_described_as_live(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    for claim in ("sünkroonitud", "API-ga ühendatud", "automaatselt uuendatud"):
        assert claim not in content


def test_value_is_never_called_revenue(client, authenticate_viewer, seeded):
    """Koda.ee records no payment receipt, so this is ordered value."""
    content = _get(client, authenticate_viewer).content.decode()

    assert "Tellitud väärtus (KM-ta)" in content
    assert "Tulu" not in content
    assert "Laekunud" not in content


def test_the_overview_counts_order_lines_not_orders(client, authenticate_viewer, seeded):
    """Summing the cell grain across products counts an order once per product.

    The synthetic package has an order carrying two different products, so the
    overview's figure must not be presented as a count of orders.
    """
    content = _get(client, authenticate_viewer).content.decode()

    assert "Tellimusridu" in content
    assert "Tellimused" not in content
    assert "loeb iga toote eraldi" in content


def test_a_single_product_may_call_them_orders(client, authenticate_viewer, seeded):
    """On one product's page the sum is over that product's cells alone."""
    authenticate_viewer(client)
    url = reverse("shop-product", args=[DOCUMENT_WITH_BOTH_PAGES])
    content = client.get(url).content.decode()

    assert "Tellimused" in content
    assert "sisaldasid seda toodet" in content


def test_the_member_split_is_withheld_until_verified(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    assert "ei ole kinnitanud" in content
    assert "Liikmete soetused" not in content


# ---------------------------------------------------------------------------
# The two page-view columns
# ---------------------------------------------------------------------------


def test_both_view_columns_are_present_and_named_apart(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    assert "Tooteleht" in content
    assert "Tutvustus" in content
    assert "ei liideta" in content


def test_an_unmeasured_figure_renders_as_a_dash(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    assert "—" in content


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


def test_period_state_survives_a_search(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    response = client.get("/epood/?periood=koik&otsing=Näidisleping")
    content = response.content.decode()

    assert response.status_code == 200
    assert "periood=koik" in content
    assert "Näidisleping" in content


def test_search_finds_a_product_by_id(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    response = client.get(f"/epood/?periood=koik&otsing={DOCUMENT_WITH_BOTH_PAGES}")

    assert response.status_code == 200
    assert "Näidisleping ühe lehega" in response.content.decode()


def test_an_unreadable_period_still_renders(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    response = client.get("/epood/?periood=eile&lk=abc&liik=miski&kategooria=xyz")

    assert response.status_code == 200


def test_an_out_of_range_page_is_clamped(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    response = client.get("/epood/?periood=koik&lk=9999")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Product detail
# ---------------------------------------------------------------------------


def test_the_detail_page_is_keyed_by_commerce_id(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    url = reverse("shop-product", args=[DOCUMENT_WITH_BOTH_PAGES])
    response = client.get(url)

    assert response.status_code == 200
    assert f"Toote ID {DOCUMENT_WITH_BOTH_PAGES}" in response.content.decode()


def test_an_unknown_product_is_a_404(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    assert client.get("/epood/toode/424242/").status_code == 404


def test_the_detail_page_states_the_price_limitation(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    url = reverse("shop-product", args=[DOCUMENT_WITH_BOTH_PAGES])
    content = client.get(url).content.decode()

    assert "Ajalooline hinnakiri ei ole säilinud" in content
    assert "liikmesoodustust ei arvutata" in content


def test_a_product_without_an_information_page_has_no_funnel(client, authenticate_viewer, seeded):
    authenticate_viewer(client)
    url = reverse("shop-product", args=[DOCUMENT_PRODUCT_PAGE_ONLY])
    content = client.get(url).content.decode()

    assert "Tutvustusest soetuseni" not in content


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_no_inline_style_reaches_the_page(client, authenticate_viewer, seeded):
    """The CSP is `style-src 'self'`; a proportion is drawn as SVG geometry."""
    content = _get(client, authenticate_viewer).content.decode()

    assert 'style="' not in content


def test_the_product_table_lets_long_titles_wrap(client, authenticate_viewer, seeded):
    """A title has no natural width; `min-w-max` would put it behind a scrollbar."""
    content = _get(client, authenticate_viewer).content.decode()

    assert "min-w-max" not in content
    assert "overflow-x-auto" in content


def test_a_stale_export_does_not_show_a_rate_for_unimported_days(
    client, authenticate_viewer, tmp_path
):
    """The clamp, as the reader sees it.

    The export stops in June; a July period must offer no web comparison rather
    than a rate computed against no orders.
    """
    manifest = {
        **default_manifest(),
        "source_as_of": "2026-06-30",
        "coverage_end": "2026-06-30",
    }
    rows = [
        {
            "report_date": "2026-06-01",
            "source_product_id": str(DOCUMENT_WITH_BOTH_PAGES),
            "commerce_state": "completed",
            "member_status": "member",
            "payment_class": "invoice",
            "order_count": "1",
            "units": "1.00",
            "ordered_value_net": "30.0000",
            "currency": "EUR",
        }
    ]
    import_shop_package(build_package(tmp_path, manifest=manifest, daily_facts=rows), dry_run=False)

    authenticate_viewer(client)
    response = client.get("/epood/?periood=kohandatud&alates=2026-07-01&kuni=2026-07-31")
    content = response.content.decode()

    assert response.status_code == 200
    assert "veebivõrdlust ei ole" in content or "veebimõõtmist ei ole" in content


def test_the_shop_appears_in_the_navigation(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()
    assert "E-pood" in content


def test_the_dates_come_from_the_data_not_the_clock(client, authenticate_viewer, seeded):
    """A preset must not drift past a frozen export.

    Thirty days against an 11 August export ends on 11 August, whatever today
    happens to be when the suite runs.
    """
    authenticate_viewer(client)
    content = client.get("/epood/?periood=30").content.decode()

    assert "11.08.2026" in content
    assert str(dt.date.today().year + 1) not in content
