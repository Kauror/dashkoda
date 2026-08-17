"""Where the newsletter material lives: Otsepostitused, and nowhere else.

This is a placement suite. What the figures *say* is covered by
`tests/visibility/test_newsletter_analytics.py` and
`tests/visibility/test_mailings_page.py`; what is pinned here is that exactly
one page renders them.

The material has moved twice. It was Nähtavus's, then Uudised's, and it is now
its own section under Koduleht. Each move left the previous page able to render
it, which is why this suite asserts absence on both:

- **Koduleht** keeps the website and has no newsletter card, section or sends
  table;
- **Uudised** keeps every article section it had — the dashboard, the one
  chart `Uudiste mõju` still drew and the archive all merged onto its one
  remaining view on 2026-08-17 — and has no newsletter focus, card, section
  or subject search;
- **Otsepostitused** has all of it: the card, the analytics, the filter, the
  subject search, the send archive and the link between the two;
- every retired address still resolves, and none of them lands anywhere but
  Otsepostitused.

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
MAILINGS_URL = "/otsepostitused/"
# The website page is Koduleht at `/koduleht/`. `/nahtavus/` still resolves and
# would answer these assertions after a redirect, but a test that follows one is
# a test that cannot tell the two apart.
VISIBILITY_URL = "/koduleht/"

#: The archive section of Uudised, which stayed — `fookus=arhiiv` retired to
#: the page's one view on 2026-08-17 and is a no-op now, kept here as a
#: stale-bookmark check. The parameter travels as an ordinary dict because
#: Django's test client discards a path's own query string as soon as `data`
#: is passed.
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
# Koduleht does not carry the newsletters
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


def test_the_overview_strip_keeps_the_social_channels(viewer_client):
    """The half that did not move, asserted where it now lives.

    "Remove the newsletter slot" and "remove a slot from the band" are one line
    apart, and the second would take the four social cards with it.

    The band left the website page when it became Koduleht — a page named after
    the website does not open with four figures about something else — so this
    asserts on the overall dashboard, where a board member reads the audiences
    together.

    The website slot was the fifth thing checked here until 2026-08-17. It went
    with the front-page rebuild: sessions are the `Koduleht ja uudised` card's
    headline now, and a visit is not an audience.
    """
    read()

    content = page(viewer_client.get(reverse("home")))

    assert "Kodulehe külastused" not in content
    for label in (
        "Facebooki jälgijad",
        "LinkedIni jälgijad",
        "Instagrami jälgijad",
        "YouTube’i tellijad",
    ):
        assert label in content, f"{label} left the strip with the newsletters"


def test_the_website_page_renders_without_ga4(viewer_client):
    response = viewer_client.get(VISIBILITY_URL)

    assert response.status_code == 200
    assert "Koduleht" in page(response)


def test_the_website_page_renders_with_newsletter_data_present(viewer_client):
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
# Uudised does not carry them either, and keeps its own archive
# ======================================================================


def test_the_news_archive_is_unchanged(viewer_client):
    """Every control the archive had while the newsletters sat beside it."""
    content = page(viewer_client.get(NEWS_URL, ARCHIVE))

    for control in (
        "Avaldamisperiood",
        "Uudise liik",
        "Järjestus:",
        "Otsi uudist",
        "Uudiste arhiiv",
    ):
        assert control in content, f"the news archive lost {control}"


def test_the_news_page_offers_no_focus_navigation(viewer_client):
    """One view, no tabs.

    `Avaldamine` folded into the overview on 2026-08-16; `Uudiskirjad` left for
    Otsepostitused before that; `Uudiste mõju` and `Arhiiv` merged into the
    same one view as `Ülevaade` on 2026-08-17, so there is nothing left to
    choose between and the tab bar itself is gone — a nav with one,
    unclickable, already-active chip in it would read as a fault.
    """
    content = page(viewer_client.get(NEWS_URL))

    assert 'aria-label="Vaade"' not in content
    for label in ("Uudiste mõju", "Arhiiv"):
        assert label not in content
    assert "fookus=uudiskirjad" not in content
    assert "fookus=avaldamine" not in content
    assert "fookus=moju" not in content
    assert "fookus=arhiiv" not in content


def test_the_news_page_has_no_newsletter_card_section_or_sends(viewer_client):
    """The move out, asserted the same way the move off Nähtavus was."""
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(NEWS_URL))

    assert CARD_HEADING not in content
    assert SECTION_HEADING not in content
    assert SENDS_HEADING not in content
    assert "Kutse ärifoorumile" not in content
    assert "Otsi uudiskirja" not in content


def test_the_news_overview_no_longer_summarises_the_newsletters(viewer_client):
    """The comparison strip went with the rest of it.

    It was three weighted rates at the foot of the news overview, and leaving it
    there would have been the duplication this move exists to end — the same
    three figures on two pages, differing the first time either changed.
    """
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(NEWS_URL))

    assert "Uudiskirjade võrdlus" not in content
    assert "Vaata uudiskirju" not in content


# ======================================================================
# Otsepostitused carries all of it
# ======================================================================


def test_the_mailings_page_shows_the_newsletter_card(viewer_client):
    read()

    content = page(viewer_client.get(MAILINGS_URL))

    assert CARD_HEADING in content
    # e-Teataja is 100 members + 200 others, counted once.
    assert "300" in content


def test_the_mailings_page_is_headed_otsepostitused(viewer_client):
    response = viewer_client.get(MAILINGS_URL)

    assert response.status_code == 200
    content = page(response)
    assert "<h1" in content
    assert "Otsepostitused" in content


def test_the_card_lists_each_newsletter_and_totals_none_of_them(viewer_client):
    """The rule that outlives every layout change.

    Three lists, three audiences, and nobody has counted how many people are on
    more than one. 300 + 30 + 40 is 370, and 370 must not appear: it would claim
    an overlap of zero that nothing has measured.
    """
    read()

    content = page(viewer_client.get(MAILINGS_URL))

    assert "300" in content
    assert "30" in content
    assert "40" in content
    assert "370" not in content


def test_a_newsletter_nobody_collected_stays_missing_rather_than_zero(viewer_client):
    """Missing is not zero, on this page as on every other."""
    read(drop=(2711,))

    content = page(viewer_client.get(MAILINGS_URL))

    assert "Sisestamata" in content
    assert "eNews" in content


def test_the_mailings_page_shows_the_analytics_section_and_recent_sends(viewer_client):
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(MAILINGS_URL))

    assert SECTION_HEADING in content
    assert SENDS_HEADING in content
    assert "Kutse ärifoorumile" in content


def test_the_mailings_page_filters_by_newsletter(viewer_client):
    read()
    send(1, "Ainult eTeatajas", newsletter=ETEATAJA)
    send(2, "Ainult eNewsis", newsletter=ENEWS)

    content = page(viewer_client.get(MAILINGS_URL, {"uudiskiri": str(ENEWS)}))

    assert "Ainult eNewsis" in content
    assert "Ainult eTeatajas" not in content


def test_the_mailings_page_searches_newsletter_subjects(viewer_client):
    read()
    send(1, "Kutse ärifoorumile")
    send(2, "Midagi muud")

    content = page(viewer_client.get(MAILINGS_URL, {"otsi": "ärifoorum"}))

    assert "Kutse ärifoorumile" in content
    assert "Midagi muud" not in content


def test_a_newsletter_search_matching_nothing_keeps_the_box(viewer_client):
    """The control that clears a search must survive the search finding nothing."""
    read()
    send(1, "Kutse ärifoorumile")

    content = page(viewer_client.get(MAILINGS_URL, {"otsi": "ei leidu midagi"}))

    assert "Otsi uudiskirja" in content
    assert "Tühjenda otsing" in content
    assert "Ühtegi saadetud uudiskirja ei leitud." in content


def test_the_section_says_so_when_nothing_has_been_collected(viewer_client):
    content = page(viewer_client.get(MAILINGS_URL))

    assert SECTION_HEADING in content
    assert "Saadetud uudiskirjad ilmuvad siia pärast esimest Smaily kogumist." in content


def test_the_mailings_page_does_not_build_the_website_analytics(viewer_client):
    """Only the newsletter data is read here.

    Reaching for the whole website page would have been the short way to the
    section, and it would have run the GA4 traffic queries, the content ranking
    and every social metric on a page that renders none of them. The website and
    social cards being absent is what that looks like from outside.
    """
    read()

    content = page(viewer_client.get(MAILINGS_URL))

    assert "Kodulehe külastused" not in content
    assert "Facebooki jälgijad" not in content


def test_the_mailings_page_marks_itself_as_the_active_section(viewer_client):
    response = viewer_client.get(MAILINGS_URL)

    assert response.context["active_nav"] == "mailings"


def test_the_mailings_page_reads_no_news_parameter(viewer_client):
    """A period or a category on this address changes nothing and is carried nowhere.

    They were meaningful while this section shared a URL with the news archive.
    It does not any more, and a link on this page that emitted `periood=1a` would
    be handing the reader an address whose own page cannot read it back.
    """
    read()
    send(1, "Aastakoosolek", newsletter=ENEWS)

    content = page(
        viewer_client.get(
            MAILINGS_URL,
            {"uudiskiri": str(ENEWS), "periood": "1a", "kategooria": "meie_uudised"},
        )
    )

    assert "Aastakoosolek" in content
    assert "periood=1a" not in content
    assert "kategooria=meie_uudised" not in content


# ======================================================================
# The send archive is the section's sends table
# ======================================================================


def test_the_canonical_archive_is_under_otsepostitused(viewer_client):
    send(1, "Kutse ärifoorumile")

    response = viewer_client.get(MAILINGS_URL)

    assert response.status_code == 200
    content = page(response)
    assert SENDS_HEADING in content
    assert "Kutse ärifoorumile" in content
    # It belongs to Otsepostitused, and no longer offers to go back to Uudised.
    assert "Tagasi uudiste lehele" not in content
    assert "Tagasi nähtavuse lehele" not in content


def test_the_old_archive_address_still_answers(viewer_client):
    """A permanent redirect, not a 404. It was linked from here for months."""
    response = viewer_client.get(reverse("mailings-history"))

    assert response.status_code == 301
    assert response.url.startswith(reverse("mailings"))


def test_the_page_offers_no_view_navigation(viewer_client):
    """One view, so no chips — and no link to the address the archive left.

    `Vaata kõiki` and the `Ülevaade` / `Saadetised` chips all pointed at
    `/otsepostitused/ajalugu/`. A link from this page to a redirect back to
    this page is a loop with an extra request in it.
    """
    read()
    for campaign_id in range(1, 20):
        send(campaign_id, f"Saadetis {campaign_id}")

    content = page(viewer_client.get(MAILINGS_URL))

    # As an `href`, not as a substring: the search fragment still lives under
    # `/otsepostitused/ajalugu/otsi/` — only htmx ever asks for it — and a bare
    # `not in` matches that path's own prefix and fails on a page that is right.
    assert f'href="{reverse("mailings-history")}"' not in content
    assert f'href="{reverse("news-newsletter-history")}"' not in content
    assert "Vaata kõiki" not in content
