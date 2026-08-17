"""What the overview band and the Nähtavus page actually show a viewer."""

from __future__ import annotations

import html as html_module
import re

import pytest
from django.urls import reverse
from django.utils.html import strip_tags

from .conftest import LEGACY_PAGE_URL, PAGE_URL

pytestmark = pytest.mark.django_db


def body(response) -> str:
    return response.content.decode()


def _card_heading(page: str, label: str) -> str:
    """The `<h3>` of one channel card, so a link assertion is about *that* card.

    Searching the whole page for an href proves only that some element somewhere
    carries it, which is how a test for "this card links here" passes because a
    different card does.

    Walks the label's occurrences rather than taking the first: a channel name
    appears in more than one place on the overview, and the first hit is not
    reliably the heading.
    """
    start = 0
    while True:
        marker = page.find(label, start)
        assert marker != -1, f"no <h3> on the page carries {label!r}"
        opening = page.rfind("<h3", 0, marker)
        closing = page.find("</h3>", opening) if opening != -1 else -1
        if opening != -1 and closing > marker:
            return page[opening:closing]
        start = marker + 1


def visible_text(response) -> str:
    """Rendered text with entities decoded, for assertions about digits.

    `strip_tags` leaves `&#x27;` intact, and the digits inside a numeric entity
    would otherwise read as a number on a page asserting it shows none.
    """
    return html_module.unescape(strip_tags(body(response)))


# ======================================================================
# The overview channel band
# ======================================================================


def test_the_band_has_every_audience_in_order(viewer_client):
    """Five audiences, newsletter first, then the four social channels.

    Six until 2026-08-17, when the website slot went. It was the front page's
    only consumer, and the rebuilt `Koduleht ja uudised` card states sessions
    over a properly measured window with a proper comparison — so the slot was
    the weaker of two statements of one measure, on one page, under two labels.
    Sessions are not an audience anyway: they are visits, and one person
    visiting twice is two of them.
    """
    page = body(viewer_client.get(reverse("home")))

    positions = [
        page.index(label)
        for label in (
            "Uudiskirjad",
            "Facebooki jälgijad",
            "LinkedIni jälgijad",
            "Instagrami jälgijad",
            "YouTube’i tellijad",
        )
    ]
    assert positions == sorted(positions), "the band is out of the required order"
    assert "Kodulehe külastused" not in page


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

    assert "4 200" in page
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

    assert "4 200" in page
    assert "Käsitsi sisestatud" not in page
    assert "Automaatselt kogutud" not in page


def test_a_typed_figure_is_never_worded_as_a_collected_one(submit, viewer_client):
    """The rule that survives, now that the definition list is gone.

    `Allikate määratlused` was struck out on the board's marked-up print, and
    with it the sentence naming each figure's source. What must still hold is
    the narrower rule from AGENTS.md: a typed figure is never *worded* as a
    feed. Which figures are typed is documented in `apps/visibility/registry.py`
    rather than restated beside every number.

    Read on the overview, which is where the typed figures are shown since the
    website page became Koduleht.
    """
    submit(facebook_followers=4200)

    page = body(viewer_client.get(reverse("home")))

    assert "4 200" in page
    for feed_word in ("sünkroonitud", "API-ga ühendatud", "automaatselt uuendatud"):
        assert feed_word not in page.lower()


def channel_band(response) -> str:
    """Just the Kanalite statistika section.

    Scoped deliberately: the legal-work card on the same page legitimately talks
    about synchronisation, because that feed genuinely is synchronised. What must
    never borrow those words is a figure somebody typed.

    It runs to the end of the body. The end marker used to be the connection
    -state strip's `id="freshness-region"`, which was removed from the overview
    on 2026-08-11; the band is now the last thing on the page.
    """
    page = body(response)
    start = page.index('aria-labelledby="section-channels"')
    return page[start:]


def test_no_card_claims_an_automatic_feed(submit, viewer_client):
    submit(facebook_followers=4200, linkedin_followers=2500)

    band = channel_band(viewer_client.get(reverse("home"))).lower()

    assert "4 200" in band, "the band is expected to contain the published figure"
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
    assert "4 200" in page, "a stale figure is still the last thing anybody counted"


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


def test_the_front_page_never_links_a_reader_into_google_analytics(viewer_client):
    """It would land a board member on a login screen.

    The website slot that carried this rule is gone; the rule is not, because
    the GA4 figures are still on the page — as the `Koduleht ja uudised` card —
    and the place to read more of them is Koduleht.
    """
    page = body(viewer_client.get(reverse("home")))

    assert "analytics.google.com" not in page
    assert f'href="{PAGE_URL}"' in page


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
    assert "1 200" in page
    assert "800" in page
    assert "150" in page
    # 2150 is the sum. It would be the audience only if nobody were on two
    # lists, and nobody has counted whether anyone is.
    assert "2150" not in page


def test_the_band_names_the_newsletters_nobody_has_entered(submit, viewer_client):
    submit(newsletter_eteataja=1200)

    page = body(viewer_client.get(reverse("home")))

    assert "1 200" in page
    # Named as unentered rather than drawn as a zero.
    assert "Sisestamata" in page
    assert "eNews" in page


# ----------------------------------------------------------------------
# Where each card's heading goes
# ----------------------------------------------------------------------
#
# The band used to be handed one URL for all six cards, which meant that
# address had to be wrong for five of them — and quietly became wrong for all
# six as material moved. Each destination is decided per slot now, and these
# are the three answers.


def test_the_newsletter_card_links_to_the_page_that_shows_newsletters(submit, viewer_client):
    """`Uudiskirjade tulemused` is on Otsepostitused.

    This card has been left pointing at the wrong page twice: at the website
    page after the newsletters left it, and at Uudised after they left there.
    Each time it stayed a working link to a page that no longer showed the
    subject, which is why the assertion names the destination rather than only
    checking that some href exists.
    """
    submit(newsletter_eteataja=1200)

    page = body(viewer_client.get(reverse("home")))
    heading = _card_heading(page, "Uudiskirjad")

    assert f'href="{reverse("mailings")}"' in heading
    assert f'href="{reverse("news")}"' not in heading


def test_the_website_figures_link_to_koduleht(submit, viewer_client, ga4_day, today, days_ago):
    """The GA4 figures on the front page offer the page that explains them.

    This was the channel band's website card until 2026-08-17. The card went;
    the route did not, because `Koduleht ja uudised` in `Põhinäitajad` carries
    the same measure and links there itself.
    """
    ga4_day(days_ago(1), sessions=120, page_views=300)

    page = body(viewer_client.get(reverse("home")))

    # The card prints the figure and its unit rather than the metric's label —
    # `Kodulehe külastused` is in the data, where `Andmete seis` reads it.
    assert "külastust" in strip_tags(page)
    assert f'href="{PAGE_URL}"' in page
    assert "Vaata kodulehte" in page


def test_the_social_cards_link_nowhere(submit, viewer_client):
    """There is no viewer-readable page of social history to point at.

    Koduleht deliberately shows none, and the admin entry list is staff-only —
    linking a viewer there would advertise a door they cannot open, which is the
    same rule that keeps `Lisa andmed` off their page. A plain heading is the
    honest state, not an oversight.
    """
    submit(
        facebook_followers=4200,
        linkedin_followers=2500,
        instagram_followers=700,
        youtube_subscribers=60,
    )

    page = body(viewer_client.get(reverse("home")))

    for label in (
        "Facebooki jälgijad",
        "LinkedIni jälgijad",
        "Instagrami jälgijad",
        "YouTube’i tellijad",
    ):
        heading = _card_heading(page, label)
        assert "<a" not in heading, f"{label} links its heading somewhere"

    # The figures themselves are still there, and the outbound profile links —
    # which are a different thing — are untouched.
    assert "4 200" in page
    assert "https://www.facebook.com/" in page


def test_no_card_points_at_a_page_that_does_not_show_it(submit, viewer_client):
    """The defect the three tests above exist to prevent, stated once.

    A heading link is a promise that the thing named is at the other end. The
    band broke that promise for five of six cards by construction, because one
    address was shared by slots describing different subjects.
    """
    submit(facebook_followers=4200, newsletter_eteataja=1200)

    page = body(viewer_client.get(reverse("home")))

    # No social card points at Koduleht, which shows no social figures.
    for label in ("Facebooki jälgijad", "LinkedIni jälgijad"):
        assert PAGE_URL not in _card_heading(page, label)
    # And the newsletter card does not either.
    assert PAGE_URL not in _card_heading(page, "Uudiskirjad")


# ======================================================================
# The Koduleht page
# ======================================================================
#
# The website surface was `Nähtavus` and carried the five-slot social band above
# a traffic section. It is `Koduleht` now and answers questions about the
# website; the social figures are untouched and are still shown on the overview
# band, which the first half of this file covers.


def test_the_page_requires_viewer_access(client):
    response = client.get(PAGE_URL)

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_a_viewer_can_open_the_page(viewer_client):
    response = viewer_client.get(PAGE_URL)

    assert response.status_code == 200
    assert "Koduleht" in body(response)


def test_the_navigation_offers_koduleht(viewer_client):
    page = body(viewer_client.get(reverse("home")))

    assert "Koduleht" in page
    assert f'href="{PAGE_URL}"' in page
    assert "Nähtavus" not in page


def test_the_old_address_still_resolves(viewer_client):
    """A board member who bookmarked the website page should arrive at it, not
    at a 404. Temporary rather than permanent: a 301 is cached indefinitely."""
    response = viewer_client.get(LEGACY_PAGE_URL)

    assert response.status_code == 302
    assert response.url.startswith(PAGE_URL)


def test_the_old_address_carries_the_saved_state_into_the_view_that_answers_it(
    viewer_client,
):
    response = viewer_client.get(f"{LEGACY_PAGE_URL}?periood=90&sisu=uudised")

    assert response.status_code == 302
    assert "periood=90" in response.url
    assert "sisu=uudised" in response.url
    # A saved section filter was somebody asking about content.
    assert "fookus=sisu" in response.url


def test_a_saved_search_lands_in_the_page_explorer(viewer_client):
    """The explorer lives at the foot of `Sisu ja lehed` since `lehed` retired."""
    response = viewer_client.get(f"{LEGACY_PAGE_URL}?otsing=liikmemaks")

    assert response.status_code == 302
    assert "fookus=sisu" in response.url
    assert "otsing=liikmemaks" in response.url


def test_the_redirect_drops_parameters_koduleht_does_not_understand(viewer_client):
    """What comes back is rebuilt from validated values, never echoed."""
    response = viewer_client.get(f"{LEGACY_PAGE_URL}?periood=90&utm_source=spam&x=1")

    assert "periood=90" in response.url
    assert "utm_source" not in response.url
    assert "x=1" not in response.url


def test_the_redirect_does_not_loop(viewer_client):
    response = viewer_client.get(LEGACY_PAGE_URL, follow=True)

    assert response.status_code == 200
    assert len(response.redirect_chain) == 1


def test_koduleht_does_not_show_the_social_channel_band(submit, viewer_client):
    """A page called Koduleht should answer questions about the website. The
    four typed figures are not deleted and are not hidden — they are on the
    overview band, and `test_the_band_has_all_six_channels_in_order` covers
    them there."""
    submit(
        facebook_followers=4200,
        linkedin_followers=2500,
        instagram_followers=700,
        youtube_subscribers=60,
    )

    page = body(viewer_client.get(PAGE_URL))

    assert "Facebook" not in page
    assert "LinkedIn" not in page
    assert "Instagram" not in page
    assert "4200" not in page


def test_koduleht_makes_no_focus_area_of_social_media(viewer_client):
    page = body(viewer_client.get(PAGE_URL))

    assert "Sotsiaalmeedia" not in page


def test_the_social_figures_still_exist_after_the_rename(submit, viewer_client, today):
    """The regression this guards: a page rename that quietly took the history
    with it. Nothing about the models, the admin or the overview changed."""
    submit(facebook_followers=4200)

    from apps.visibility.selectors import get_visibility_summary

    summary = get_visibility_summary(today=today)
    facebook = next(r for r in summary.social if "Facebook" in r.label)

    assert facebook.value == 4200
    # Grouped on the way out: the card renders the measured integer as 4 200.
    assert "4 200" in body(viewer_client.get(reverse("home")))


def test_manual_entry_is_no_longer_a_primary_koduleht_action(submit, staff_client):
    """Still reachable for staff, and now one page further out.

    It sat in `Andmete kohta` at the foot of Koduleht; that whole block moved to
    `/haldus/` on 2026-08-16 and the link went with it. Both halves are asserted,
    because a link dropped in the move would pass the Koduleht side on its own.
    """
    submit(facebook_followers=4200)

    koduleht = body(staff_client.get(PAGE_URL))
    admin = body(staff_client.get("/haldus/"))

    assert "/admin/data-entry/visibility/new/" not in koduleht
    assert "Andmete kohta" not in koduleht
    assert "/admin/data-entry/visibility/new/" in admin


def test_an_ordinary_viewer_sees_no_editing_control(submit, viewer_client):
    submit(facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert "Lisa andmed" not in page
    assert "/admin/data-entry/" not in page


def test_the_page_does_not_name_who_entered_a_figure(submit, viewer_client, staff_user):
    """Who typed it is a staff detail and belongs in the admin history."""
    submit(facebook_followers=4200, actor=staff_user)

    page = body(viewer_client.get(PAGE_URL))

    assert staff_user.username not in page


def test_the_page_carries_no_inline_style_and_no_external_asset(submit, viewer_client):
    """`style-src 'self'` and `script-src 'self'`: a bar length or a line
    position may never be an inline width, and no asset may come from a CDN."""
    submit(facebook_followers=4200)

    page = body(viewer_client.get(PAGE_URL))

    assert 'style="' not in page
    assert "<script>" not in page
    for match in re.findall(r'href="(https://[^"]+)"', page):
        assert match.startswith("https://www.koda.ee/"), match


def test_an_empty_page_says_what_is_missing_rather_than_showing_zeros(viewer_client):
    """No collected day is not a day of no traffic."""
    page = body(viewer_client.get(PAGE_URL))

    assert "ei ole veel kogutud" in page
    assert "charts.js" not in page


def test_an_empty_page_ships_no_chart_bundle(viewer_client):
    """The bundle loads only when the current view has something to draw."""
    assert "build/charts.js" not in body(viewer_client.get(PAGE_URL))
