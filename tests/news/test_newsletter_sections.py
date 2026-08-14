"""Where the newsletter material lives: Uudised, and no longer Nähtavus.

This is a placement suite. What the figures *say* is covered by
`tests/visibility/test_newsletter_analytics.py`, which still owns the arithmetic
because `apps.visibility` still owns the data. What is pinned here is the move
itself, in both directions:

- Nähtavus keeps the website and the social channels and has no newsletter
  section, no newsletter card and no sends table;
- Uudised keeps its archive exactly as it was and gains the card, the section,
  the filter, the subject search and the link to the full archive;
- neither section resets the other. Two independent sets of query parameters
  share one page, and a reader narrowing one must not lose the other.

The empty states matter as much as the populated ones: a newsletter nobody has
collected must read as missing, never as zero.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import SmailyCampaign, VisibilityMetric
from apps.visibility.smaily import SegmentReading, SegmentRow
from apps.visibility.smaily_sync import synchronize_smaily

pytestmark = pytest.mark.django_db

ETEATAJA = VisibilityMetric.NEWSLETTER_ETEATAJA
ENEWS = VisibilityMetric.NEWSLETTER_ENEWS

DAY = dt.date(2026, 7, 1)

NEWS_URL = "/uudised/"
VISIBILITY_URL = "/nahtavus/"

#: Uudised now carries five focus views over one address. The newsletter
#: material is the `uudiskirjad` focus and the archive is `arhiiv` — both still
#: on Uudised, both still absent from Nähtavus, which is the move this suite
#: exists to pin. The focus travels as an ordinary parameter because Django's
#: test client discards a path's own query string as soon as `data` is passed.
NEWSLETTERS = {"fookus": "uudiskirjad"}
ARCHIVE = {"fookus": "arhiiv"}

#: The section headings each page is asserted to hold or not hold.
CARD_HEADING = "Uudiskirjad"
SECTION_HEADING = "Uudiskirjade tulemused"
SENDS_HEADING = "Saadetud uudiskirjad"


class FakeCollector:
    def __init__(self, segments):
        self.segments = segments

    def collect_segments(self, *, observed_on=None):
        return SegmentReading(observed_on=observed_on, segments=self.segments).validate()


def read(day=DAY, *, members=100, others=200, enews=30, evestnik=40, drop=()):
    """One day's subscriber reading, as the Smaily collector would leave it."""
    rows = [
        SegmentRow(2690, "E-teataja list", members),
        SegmentRow(2691, "E-teataja list mitteliikmed", others),
        SegmentRow(2711, "E-News list", enews),
        SegmentRow(2692, "E-vestnik list - liikmed ja mitteliikmed koos", evestnik),
    ]
    rows = tuple(row for row in rows if row.segment_id not in drop)
    synchronize_smaily(observed_on=day, collector=FakeCollector(rows))


def send(campaign_id, name, *, newsletter=ETEATAJA, days_ago=1):
    """One completed campaign. No statistics: placement does not need them."""
    return SmailyCampaign.objects.create(
        campaign_id=campaign_id,
        name=name,
        template_name="e-Teataja",
        newsletter=newsletter,
        status="COMPLETED",
        completed_at=timezone.now() - dt.timedelta(days=days_ago),
    )


def page(response) -> str:
    return response.content.decode()


# ======================================================================
# Nähtavus no longer carries the newsletters
# ======================================================================


def test_the_visibility_page_has_no_newsletter_card(viewer_client):
    read()

    content = page(viewer_client.get(VISIBILITY_URL))

    assert CARD_HEADING not in content


def test_the_visibility_page_has_no_newsletter_section_or_sends(viewer_client):
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(VISIBILITY_URL))

    assert SECTION_HEADING not in content
    assert SENDS_HEADING not in content
    assert "Kutse ärifoorumile" not in content
    # The subject search went with the table it filtered.
    assert "Otsi uudiskirja" not in content


def test_the_visibility_page_keeps_the_website_and_the_social_channels(viewer_client):
    """The half of the page that did not move.

    Asserted explicitly because "remove the newsletter slot" and "remove a slot
    from the band" are one line apart, and the second would take the website and
    four social cards with it.
    """
    read()

    content = page(viewer_client.get(VISIBILITY_URL))

    assert "Kodulehe külastused" in content
    for label in (
        "Facebooki jälgijad",
        "LinkedIni jälgijad",
        "Instagrami jälgijad",
        "YouTube’i tellijad",
    ):
        assert label in content, f"{label} left the band with the newsletters"


def test_the_visibility_page_renders_without_ga4(viewer_client):
    response = viewer_client.get(VISIBILITY_URL)

    assert response.status_code == 200
    assert "Kodulehe külastused" in page(response)


def test_the_visibility_page_renders_with_newsletter_data_present(viewer_client):
    """Collected newsletter data must not put the section back.

    The queries still run for the overall dashboard's band, so the failure this
    guards against is the page rendering a section whose data merely exists.
    """
    read()
    send(1, "Kutse ärifoorumile")

    response = viewer_client.get(VISIBILITY_URL)

    assert response.status_code == 200
    assert SECTION_HEADING not in page(response)


# ======================================================================
# Uudised carries them now, and keeps its own archive
# ======================================================================


def test_the_news_archive_is_unchanged(viewer_client):
    """Every control the archive had before the newsletters arrived below it."""
    content = page(viewer_client.get(NEWS_URL, ARCHIVE))

    for control in (
        "Avaldamisperiood",
        "Uudise liik",
        "Järjestus:",
        "Otsi uudist",
        "Uudiste arhiiv",
    ):
        assert control in content, f"the news archive lost {control}"


def test_the_news_page_shows_the_newsletter_card(viewer_client):
    read()

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert CARD_HEADING in content
    # e-Teataja is 100 members + 200 others, counted once.
    assert "300" in content


def test_the_card_lists_each_newsletter_and_totals_none_of_them(viewer_client):
    """The rule that outlives every layout change.

    Three lists, three audiences, and nobody has counted how many people are on
    more than one. 300 + 30 + 40 is 370, and 370 must not appear: it would claim
    an overlap of zero that nothing has measured.
    """
    read()

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert "300" in content
    assert "30" in content
    assert "40" in content
    assert "370" not in content


def test_a_newsletter_nobody_collected_stays_missing_rather_than_zero(viewer_client):
    """Missing is not zero, on this page as on every other."""
    read(drop=(2711,))

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert "Sisestamata" in content
    assert "eNews" in content


def test_the_news_page_shows_the_analytics_section_and_recent_sends(viewer_client):
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert SECTION_HEADING in content
    assert SENDS_HEADING in content
    assert "Kutse ärifoorumile" in content


def test_the_news_page_filters_by_newsletter(viewer_client):
    read()
    send(1, "Ainult eTeatajas", newsletter=ETEATAJA)
    send(2, "Ainult eNewsis", newsletter=ENEWS)

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS | {"uudiskiri": str(ENEWS)}))

    assert "Ainult eNewsis" in content
    assert "Ainult eTeatajas" not in content


def test_the_news_page_searches_newsletter_subjects(viewer_client):
    read()
    send(1, "Kutse ärifoorumile")
    send(2, "Midagi muud")

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS | {"otsi": "ärifoorum"}))

    assert "Kutse ärifoorumile" in content
    assert "Midagi muud" not in content


def test_a_newsletter_search_matching_nothing_keeps_the_box(viewer_client):
    """The control that clears a search must survive the search finding nothing."""
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS | {"otsi": "ei leidu midagi"}))

    assert "Otsi uudiskirja" in content
    assert "Tühjenda otsing" in content
    assert "Ühtegi saadetud uudiskirja ei leitud." in content


def test_the_section_says_so_when_nothing_has_been_collected(viewer_client):
    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert SECTION_HEADING in content
    assert "Saadetud uudiskirjad ilmuvad siia pärast esimest Smaily kogumist." in content


def test_the_news_page_does_not_build_the_visibility_analytics(viewer_client):
    """Only the newsletter data is read here.

    `build_visibility_page` would have been the short way to reach the section,
    and it would have run the GA4 traffic queries, the content ranking and every
    social metric on a page that renders none of them. The website and social
    cards being absent is what that looks like from outside.
    """
    read()

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert "Kodulehe külastused" not in content
    assert "Facebooki jälgijad" not in content


# ======================================================================
# The two sections share a page and do not reset each other
# ======================================================================

COMBINED = {
    "periood": "1a",
    "kategooria": "meie_uudised",
    "sort": "vaadatud",
    "otsing": "eksport",
    "uudiskiri": str(ENEWS),
    "otsi": "aastakoosolek",
}


def test_each_focus_reads_its_own_parameters(viewer_client):
    """The two sections no longer share a screen, and still share a URL.

    One query string carrying both sets: the newsletter focus answers
    `uudiskiri` and `otsi`, the archive focus answers `otsing`, and neither
    reaches for the other's.
    """
    read()
    send(1, "Aastakoosolek", newsletter=ENEWS)
    send(2, "Muu saadetis", newsletter=ENEWS)

    newsletters = page(viewer_client.get(NEWS_URL, NEWSLETTERS | COMBINED))
    archive = page(viewer_client.get(NEWS_URL, ARCHIVE | COMBINED))

    assert "Aastakoosolek" in newsletters
    assert "Muu saadetis" not in newsletters
    assert 'value="eksport"' in archive


def test_a_newsletter_chip_keeps_the_news_archive(viewer_client):
    """A control in one focus carries the other focus's state.

    Stronger than when the two shared a page: the archive is not rendered here
    at all, so a match can only have come from the newsletter section's own
    links. Before the split this had to be asserted on half of the markup,
    because both halves emitted their own parameters and "somewhere on the page"
    was true for free.
    """
    read()
    send(1, "Aastakoosolek", newsletter=ENEWS)

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS | COMBINED))

    assert "periood=1a" in content
    assert "kategooria=meie_uudised" in content
    assert "sort=vaadatud" in content
    assert "otsing=eksport" in content


def test_a_news_chip_keeps_the_newsletter(viewer_client):
    """And the same in the other direction, from the archive focus."""
    read()
    send(1, "Aastakoosolek", newsletter=ENEWS)

    content = page(viewer_client.get(NEWS_URL, ARCHIVE | COMBINED))

    assert f"uudiskiri={ENEWS}" in content
    assert "otsi=aastakoosolek" in content


def test_an_untouched_newsletter_section_adds_nothing_to_the_news_links(viewer_client):
    """A default section carries nothing.

    Otherwise every period chip on an ordinary visit would grow a
    `uudiskiri=koik` that says only "the reader has not chosen a newsletter".
    """
    content = page(viewer_client.get(NEWS_URL, ARCHIVE))

    assert "uudiskiri=" not in content


def test_the_focus_survives_every_archive_control(viewer_client):
    """A period chip must not quietly return the reader to the overview.

    Every archive control rebuilds its URL from validated values, so the focus
    has to be among them — otherwise narrowing the archive navigates out of it.
    """
    content = page(viewer_client.get(NEWS_URL, ARCHIVE | {"periood": "1a"}))

    assert content.count("fookus=arhiiv") >= 5


def test_the_two_searches_are_never_the_same_box(viewer_client):
    """`otsing` searches articles and `otsi` searches campaign subjects.

    Feeding either to the other would look right on this page — both parameters
    exist and both hold a string — and would silently empty the other section.
    """
    read()
    send(1, "Kutse ärifoorumile")

    # The news search must leave the sends alone.
    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS | {"otsing": "ärifoorum"}))

    assert "Kutse ärifoorumile" in content


# ======================================================================
# The full archive is a news page, and the old address still works
# ======================================================================


def test_the_canonical_archive_is_under_uudised(viewer_client):
    send(1, "Kutse ärifoorumile")

    response = viewer_client.get(reverse("news-newsletter-history"))

    assert response.status_code == 200
    content = page(response)
    assert SENDS_HEADING in content
    assert "Kutse ärifoorumile" in content
    # It belongs to Uudised now, and says so in both places a reader looks.
    assert "Tagasi uudiste lehele" in content
    assert "Tagasi nähtavuse lehele" not in content


def test_the_archive_marks_news_as_the_active_section(viewer_client):
    response = viewer_client.get(reverse("news-newsletter-history"))

    assert response.context["active_nav"] == "news"


def test_the_old_archive_url_redirects_and_keeps_the_question(viewer_client):
    """A saved bookmark is not a 404, and not a reset either.

    `uudiskiri`, `otsi` and `lk` are exactly what an archive bookmark carries,
    and dropping them would land the reader in fourteen unfiltered years.
    """
    response = viewer_client.get(
        reverse("visibility-campaign-history"),
        {"uudiskiri": str(ENEWS), "otsi": "aastakoosolek", "lk": "3"},
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("news-newsletter-history"))
    assert f"uudiskiri={ENEWS}" in response.url
    assert "otsi=aastakoosolek" in response.url
    assert "lk=3" in response.url


def test_the_old_archive_url_redirects_without_a_query_too(viewer_client):
    response = viewer_client.get(reverse("visibility-campaign-history"))

    assert response.status_code == 302
    assert response.url == reverse("news-newsletter-history")


def test_the_old_archive_search_url_still_answers(viewer_client):
    """Kept as an alias rather than deleted: htmx follows the redirect, so the
    fragment that answers is the news one."""
    send(1, "Kutse ärifoorumile")

    response = viewer_client.get(
        reverse("visibility-campaign-history-search"), {"otsi": "ärifoorum"}, follow=True
    )

    assert response.status_code == 200
    assert "Kutse ärifoorumile" in page(response)


def test_the_recent_sends_link_to_the_news_archive(viewer_client):
    """`Vaata kõiki` must not point at the old visibility route."""
    read()
    for campaign_id in range(1, 20):
        send(campaign_id, f"Saadetis {campaign_id}")

    content = page(viewer_client.get(NEWS_URL, NEWSLETTERS))

    assert reverse("news-newsletter-history") in content
