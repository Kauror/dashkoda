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
    """One quiet metadata line, not a paragraph of methodology."""
    content = _get(client, authenticate_viewer).content.decode()

    assert "Andmed 11.08.2026" in content
    assert "Tellimused 22.10.2020" in content
    assert "Andmete kohta" in content


def test_the_dataset_is_never_described_as_live(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    for claim in ("sünkroonitud", "API-ga ühendatud", "automaatselt uuendatud"):
        assert claim not in content


def test_value_is_never_called_revenue(client, authenticate_viewer, seeded):
    """Koda.ee records no payment receipt, so this is ordered value."""
    content = _get(client, authenticate_viewer).content.decode()

    assert "Tellitud väärtus" in content
    assert "KM-ta" in content
    assert "Tulu" not in content
    assert "Laekunud" not in content
    assert "Müük" not in content


def test_the_overview_says_orders_once_distinct_counts_are_imported(
    client, authenticate_viewer, seeded
):
    """Schema 2.0 carries distinct order counts, so the label may say orders.

    Naming the rendered KPI label rather than the bare word, as #112 established
    on the test this one replaced. The redesign made that necessary a second
    time over: the metadata line now reads „Tellimused 22.10.2020", so a
    substring check for the bare word passes whatever the card is labelled.
    """
    content = _get(client, authenticate_viewer).content.decode()

    assert ">Tellimused</h3>" in content
    assert ">Tellimusridu</h3>" not in content


def test_the_overview_falls_back_to_order_lines_without_distinct_counts(
    client, authenticate_viewer, tmp_path
):
    """A dataset published from schema 1.0 cannot claim distinct orders."""
    manifest = {**default_manifest(), "schema_version": "1.0"}
    import_shop_package(build_package(tmp_path, manifest=manifest), dry_run=False)

    content = _get(client, authenticate_viewer).content.decode()

    assert ">Tellimusridu</h3>" in content
    assert ">Tellimused</h3>" not in content
    assert "eri tellimuste arv ei ole imporditud" in content


def test_only_three_commerce_kpis_carry_the_headline(client, authenticate_viewer, seeded):
    """A fourth equal-weight card would make the web caveats headline material."""
    content = _get(client, authenticate_viewer).content.decode()
    strip = content.split('id="section-kpis"')[1].split("</section>")[0]

    assert strip.count("text-metric") == 3


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


def test_the_explorer_drops_the_information_column(client, authenticate_viewer, seeded):
    """Information-page traffic is depth; it lives on the product's own page."""
    content = _get(client, authenticate_viewer).content.decode()
    explorer = content.split('id="tooted"')[1]

    assert "Tutvustus" not in explorer
    assert "Vaatamised" in explorer


def test_the_two_page_rule_is_stated_in_the_methodology(client, authenticate_viewer, seeded):
    content = _get(client, authenticate_viewer).content.decode()

    assert "ei liideta" in content
    assert "<details" in content


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
    """A title has no natural width; `min-w-max` would put it behind a scrollbar.

    Scoped to the explorer rather than the whole page. The shared trend chart
    carries its own accompanying table, and that one *is* a row of figures with
    a natural width, so `min-w-max` is right there and wrong here.
    """
    content = _get(client, authenticate_viewer).content.decode()
    explorer = content.split('id="tooted"')[1]

    assert "min-w-max" not in explorer
    assert "overflow-x-auto" in explorer


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
    # Scoped to the web section, and asserting the absence of the rate rather
    # than only the presence of a sentence. A refusal that still printed a
    # number underneath it would pass a wording check and fail the reader.
    web = content.split('aria-labelledby="section-web"')[1].split("</section>")[0]
    assert "Veebivõrdlus ei ole selle perioodi kohta võimalik" in web
    assert "Soetusi / 100" not in web


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
