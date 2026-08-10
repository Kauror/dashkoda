"""What the overview band and the Nähtavus page actually show a viewer."""

from __future__ import annotations

import html as html_module
import re

import pytest
from django.urls import reverse
from django.utils.html import strip_tags

from .conftest import PAGE_URL

pytestmark = pytest.mark.django_db


def body(response) -> str:
    return response.content.decode()


def visible_text(response) -> str:
    """Rendered text with entities decoded, for assertions about digits.

    `strip_tags` leaves `&#x27;` intact, and the digits inside a numeric entity
    would otherwise read as a number on a page asserting it shows none.
    """
    return html_module.unescape(strip_tags(body(response)))


# ======================================================================
# The overview channel band
# ======================================================================


def test_the_band_has_all_six_channels_in_order(viewer_client):
    page = body(viewer_client.get(reverse("home")))

    positions = [
        page.index(label)
        for label in (
            "Kodulehe külastused",
            "Uudiskirjad",
            "Facebooki jälgijad",
            "LinkedIni jälgijad",
            "Instagrami jälgijad",
            "YouTube’i tellijad",
        )
    ]
    assert positions == sorted(positions), "the band is out of the required order"


def test_instagram_is_present(viewer_client):
    assert "Instagrami jälgijad" in body(viewer_client.get(reverse("home")))


def test_an_unpopulated_channel_shows_no_number(viewer_client):
    text = visible_text(viewer_client.get(reverse("home")))

    # The freshness region carries the connection-check time; the band itself
    # must contribute no digit at all.
    assert "Andmed puuduvad" in text
    assert re.search(r"\d+\s*(jälgijat|tellijat|saajat)", text) is None


def test_no_channel_shows_a_zero_when_it_has_no_data(viewer_client):
    """A zero here would claim the Chamber has no followers."""
    page = body(viewer_client.get(reverse("home")))

    assert ">0<" not in page


def test_a_published_value_replaces_the_empty_slot(submit, viewer_client, today):
    submit(facebook_followers=4200)

    page = body(viewer_client.get(reverse("home")))

    assert "4200" in page
    assert f"{today.day}.{today:%m.%y}" in page


def test_the_band_carries_no_provenance_caption(submit, viewer_client):
    """The chips are gone from the overview at the board's request.

    Six of them across one row said the same three things repeatedly and
    crowded out the six figures the row exists to show. Where a number came
    from is a real question, and it is answered on the Nähtavus page — see
    `test_the_visibility_page_still_states_how_each_figure_was_collected`,
    which is where that guarantee now lives.
    """
    submit(facebook_followers=4200)

    page = body(viewer_client.get(reverse("home")))

    assert "4200" in page
    assert "Käsitsi sisestatud" not in page
    assert "Automaatselt kogutud" not in page


def test_the_visibility_page_still_states_how_each_figure_was_collected(submit, viewer_client):
    """The guarantee the overview used to carry, kept where it belongs.

    A dashboard mixing typed figures with synchronised feeds has to say which
    is which somewhere, or a number a person read off a screen last month looks
    exactly like one a collector fetched this morning."""
    submit(facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert "Väärtus sisestatakse käsitsi" in page


def channel_band(response) -> str:
    """Just the Kanalite statistika section.

    Scoped deliberately: the legal-work card on the same page legitimately talks
    about synchronisation, because that feed genuinely is synchronised. What must
    never borrow those words is a figure somebody typed.
    """
    page = body(response)
    start = page.index('aria-labelledby="section-channels"')
    return page[start : page.index('id="freshness-region"')]


def test_no_card_claims_an_automatic_feed(submit, viewer_client):
    submit(facebook_followers=4200, linkedin_followers=2500)

    band = channel_band(viewer_client.get(reverse("home"))).lower()

    assert "4200" in band, "the band is expected to contain the published figure"
    assert "sünkroon" not in band
    assert "api-ga ühendatud" not in band
    assert "automaatselt uuendatud" not in band
    # And no caption of any kind now: what must never happen is a typed figure
    # borrowing the vocabulary of a synchronised one, which is still true when
    # neither says anything.
    assert "käsitsi sisestatud" not in band


def test_a_stale_reading_is_marked_on_the_band(submit, viewer_client, days_ago):
    from apps.visibility.registry import SOCIAL_STALE_AFTER_DAYS

    submit(observation_date=days_ago(SOCIAL_STALE_AFTER_DAYS + 1), facebook_followers=4200)

    page = body(viewer_client.get(reverse("home")))

    assert "Vajab uuendamist" in page
    assert "4200" in page, "a stale figure is still the last thing anybody counted"


def test_each_social_card_links_to_the_correct_public_page(submit, viewer_client):
    submit(
        facebook_followers=4200,
        linkedin_followers=2500,
        instagram_followers=700,
        youtube_subscribers=60,
    )

    page = body(viewer_client.get(reverse("home")))

    assert 'href="https://www.facebook.com/Kaubanduskoda"' in page
    assert 'href="https://www.linkedin.com/company/ecci/"' in page
    assert 'href="https://www.instagram.com/kaubanduskoda"' in page
    assert 'href="https://www.youtube.com/user/Kaubanduskoda"' in page
    assert 'rel="noopener noreferrer"' in page
    # Opened in a new tab, at the board's request: every link that leaves
    # DashKoda does. The `rel="noopener noreferrer"` asserted above is what
    # makes that safe, and the card's own `sr-only` note is what announces it.
    assert 'target="_blank"' in page


def test_an_outbound_link_says_it_leaves_dashkoda(submit, viewer_client):
    submit(facebook_followers=4200)

    page = body(viewer_client.get(reverse("home")))

    assert "väline leht, avaneb uuel vahelehel" in page


def test_no_search_index_linkedin_figure_is_hard_coded():
    """A planning-time search result is not production data.

    3 992 was what a public search index showed while this was being designed.
    It must not appear in any model, migration, fixture, selector or template.

    Walks the tree rather than shelling out to `git grep`: this suite also runs
    inside the production image, which has no git — and should not gain one just
    so a test can run.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    searchable = {".py", ".html", ".css", ".js", ".json", ".txt"}
    offenders = [
        str(path.relative_to(root))
        for directory in ("apps", "config", "templates")
        for path in (root / directory).rglob("*")
        if path.is_file()
        and path.suffix in searchable
        and any(
            needle in path.read_text(encoding="utf-8", errors="ignore")
            for needle in ("3992", "3 992")
        )
    ]

    assert offenders == [], offenders


def test_the_website_slot_stays_planned_and_links_nowhere(viewer_client):
    page = body(viewer_client.get(reverse("home")))

    assert "Kodulehe külastused" in page
    assert "Google Analytics ei ole ühendatud." in page
    assert "analytics.google.com" not in page


def test_the_website_slot_shows_no_value_even_when_other_channels_do(submit, viewer_client):
    submit(facebook_followers=4200)

    page = body(viewer_client.get(reverse("home")))
    band = page[page.index("Kodulehe külastused") : page.index("Uudiskirjad")]

    assert re.search(r"\d", strip_tags(band)) is None


# -- the newsletter slot ------------------------------------------------


def test_the_band_lists_each_newsletter_and_totals_none_of_them(submit, viewer_client):
    submit(
        newsletter_eteataja=1200,
        newsletter_enews=800,
        newsletter_evestnik=150,
    )

    page = body(viewer_client.get(reverse("home")))

    for label in ("e-Teataja", "eNews", "e-Vestnik"):
        assert label in page
    assert "1200" in page
    assert "800" in page
    assert "150" in page
    # 2150 is the sum. It would be the audience only if nobody were on two
    # lists, and nobody has counted whether anyone is.
    assert "2150" not in page


def test_the_band_names_the_newsletters_nobody_has_entered(submit, viewer_client):
    submit(newsletter_eteataja=1200)

    page = body(viewer_client.get(reverse("home")))

    assert "1200" in page
    # Named as unentered rather than drawn as a zero.
    assert "Sisestamata" in page
    assert "eNews" in page


def test_the_newsletter_slot_links_to_the_visibility_page(submit, viewer_client):
    submit(newsletter_eteataja=1200)

    assert f'href="{PAGE_URL}"' in body(viewer_client.get(reverse("home")))


# ======================================================================
# The Nähtavus page
# ======================================================================


def test_the_page_requires_viewer_access(client):
    response = client.get(PAGE_URL)

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_a_viewer_can_open_the_page(viewer_client):
    response = viewer_client.get(PAGE_URL)

    assert response.status_code == 200
    assert "Mõju ja nähtavus" in body(response)


def test_the_navigation_offers_nahtavus(viewer_client):
    page = body(viewer_client.get(reverse("home")))

    assert "Nähtavus" in page
    assert f'href="{PAGE_URL}"' in page


def test_the_page_shows_the_latest_value_for_each_channel(submit, viewer_client, today):
    submit(
        facebook_followers=4200,
        linkedin_followers=2500,
        instagram_followers=700,
        youtube_subscribers=60,
    )

    page = body(viewer_client.get(PAGE_URL))

    for value in ("4200", "2500", "700", "60"):
        assert value in page
    assert f"{today.day}.{today:%m.%y}" in page


def test_the_page_states_the_newsletter_definition(viewer_client):
    page = body(viewer_client.get(PAGE_URL))

    assert "tellijate arv smailys" in page.lower()
    assert "ei ole saadetud" in page.lower()


def test_the_page_states_each_social_definition(viewer_client):
    page = body(viewer_client.get(PAGE_URL))

    assert "Allikate määratlused" in page
    assert "jälgijate arv" in page.lower()


def test_a_correction_replaces_the_figure_rather_than_appearing_beside_it(
    submit, viewer_client, today
):
    """The observation-history table is gone, so a corrected figure has to be
    unambiguous: the page shows what is true now and not both readings.

    The superseded row is not deleted — it is still stored, still marked, and
    still readable in the admin, which is where a correction gets audited. This
    asserts only that the page stopped printing it."""
    submit(facebook_followers=4200)
    submit(facebook_followers=4250)

    page = body(viewer_client.get(PAGE_URL))

    assert "4250" in page
    assert "4200" not in page
    assert "Vaatluste ajalugu" not in page
    assert "Asendatud" not in page


def test_a_trend_needs_at_least_two_observations(submit, viewer_client, days_ago):
    submit(observation_date=days_ago(30), facebook_followers=4100)

    page = body(viewer_client.get(PAGE_URL))

    assert "<polyline" not in page
    assert "Trendi kuvamiseks on vaja vähemalt kahte vaatlust." in page


def test_two_observations_draw_a_sparkline_with_an_accessible_table(
    submit, viewer_client, today, days_ago
):
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert "<polyline" in page
    assert "Andmed tabelina" in page
    assert 'role="img"' in page
    assert "vaatlust, väikseim" in page


def test_the_page_loads_no_chart_bundle(submit, viewer_client, today, days_ago):
    """Four small follower histories do not justify a megabyte of ECharts."""
    submit(observation_date=days_ago(30), facebook_followers=4100)
    submit(observation_date=today, facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert "charts.js" not in page


def test_the_page_carries_no_inline_style_and_no_external_asset(submit, viewer_client):
    submit(facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert 'style="' not in page
    assert "<script>" not in page
    # The only absolute URLs are the four fixed public profile links.
    for match in re.findall(r'href="(https://[^"]+)"', page):
        assert match.startswith(
            (
                "https://www.facebook.com/",
                "https://www.linkedin.com/",
                "https://www.instagram.com/",
                "https://www.youtube.com/",
            )
        ), match


def test_an_ordinary_viewer_sees_no_editing_control(submit, viewer_client):
    submit(facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert "Lisa andmed" not in page
    assert "/admin/data-entry/" not in page


def test_a_staff_user_sees_the_add_action(submit, staff_client):
    submit(facebook_followers=4200)

    page = body(staff_client.get(PAGE_URL))

    assert "Lisa andmed" in page
    assert "/admin/data-entry/visibility/new/" in page


def test_the_page_does_not_name_who_entered_a_figure(submit, viewer_client, staff_user):
    """Who typed it is a staff detail and belongs in the admin history."""
    submit(facebook_followers=4200, actor=staff_user)

    page = body(viewer_client.get(PAGE_URL))

    assert staff_user.username not in page


def test_the_page_says_google_analytics_is_not_connected(viewer_client):
    page = body(viewer_client.get(PAGE_URL))

    assert "Google Analytics ei ole ühendatud." in page
    assert "Lisamisel" in page


def test_an_empty_page_says_so_rather_than_showing_zeros(viewer_client):
    """An unentered metric has to read as unmeasured, never as a zero.

    This used to lean on the observation-history table's empty state as well.
    That table is gone, so the guarantee now rests where it belongs: on the
    cards themselves, each of which says it has nothing rather than showing 0.
    """
    page = body(viewer_client.get(PAGE_URL))

    assert "Andmed puuduvad." in page
    # Every card shows the muted em-dash placeholder and none shows a figure.
    # Checking the element rather than the text "0": a card that rendered a zero
    # would use the value span, whatever the digits happened to be.
    assert "text-metric font-semibold tracking-tight text-text-muted" in page
    assert 'tabular-nums text-text">' not in page
