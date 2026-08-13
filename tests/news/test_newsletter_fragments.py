"""The two newsletter live-search fragments, now that they answer for Uudised.

They came from `tests/visibility/test_search_fragments.py` with the routes they
serve. The contract they held there is unchanged — a fragment is a fragment, it
narrows by the same term the form would submit, it resets pagination, and it is
behind the viewer gate — and one thing is added: **the URL they push is
`/uudised/`, never `/nahtavus/`**. That is the whole reason the routes moved, so
it is asserted rather than assumed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import SmailyCampaign, VisibilityMetric

pytestmark = pytest.mark.django_db

NEWSLETTER_SEARCH = "news-newsletter-search"
ARCHIVE_SEARCH = "news-newsletter-history-search"

ETEATAJA = VisibilityMetric.NEWSLETTER_ETEATAJA
ENEWS = VisibilityMetric.NEWSLETTER_ENEWS


def send(campaign_id, name, *, newsletter=ETEATAJA, days_ago=1):
    return SmailyCampaign.objects.create(
        campaign_id=campaign_id,
        name=name,
        template_name="e-Teataja",
        newsletter=newsletter,
        status="COMPLETED",
        completed_at=timezone.now() - dt.timedelta(days=days_ago),
    )


# -- the newsletter sends box ------------------------------------------------


def test_the_newsletter_fragment_is_a_fragment(viewer_client):
    send(1, "Kutse ärifoorumile")

    content = viewer_client.get(reverse(NEWSLETTER_SEARCH)).content.decode()

    assert "Kutse ärifoorumile" in content
    for shell in ("<html", "<body", "Peamenüü", "Koja töölaud"):
        assert shell not in content
    # The box itself must never come back in the swap: htmx replaces this
    # region's contents, and an input inside it loses the caret on every
    # keystroke.
    assert 'type="search"' not in content
    assert "<form" not in content


def test_the_newsletter_fragment_narrows_by_term_and_newsletter(viewer_client):
    send(1, "Aastakoosolek", newsletter=ETEATAJA)
    send(2, "Aastakoosolek", newsletter=ENEWS)
    send(3, "Midagi muud", newsletter=ETEATAJA)

    both = viewer_client.get(reverse(NEWSLETTER_SEARCH), {"otsi": "aastakoosolek"})
    assert both.content.decode().count("Aastakoosolek") >= 2

    narrowed = viewer_client.get(
        reverse(NEWSLETTER_SEARCH), {"otsi": "aastakoosolek", "uudiskiri": str(ENEWS)}
    ).content.decode()
    assert "Aastakoosolek" in narrowed
    assert "Midagi muud" not in narrowed


def test_the_newsletter_fragment_pushes_uudised_and_never_nahtavus(viewer_client):
    """The point of the move, in one assertion.

    The fragment used to push `/nahtavus/`. Left that way, a reader typing in the
    newsletter box on the news page would have watched their address bar change
    to a page that no longer has a newsletter box.
    """
    response = viewer_client.get(reverse(NEWSLETTER_SEARCH), {"otsi": "ärifoorum"})

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("news"))
    assert not pushed.startswith(reverse("visibility"))
    assert "otsi=%C3%A4rifoorum" in pushed
    assert pushed.endswith("#section-newsletter-analytics")


def test_the_newsletter_fragment_carries_the_news_archive(viewer_client):
    """Typing a subject must not reset the archive above it.

    The period, the category, the ordering and the news search are all on the
    same page and none of them is in this form, so they come from
    `HX-Current-URL` and have to survive the keystroke.
    """
    response = viewer_client.get(
        reverse(NEWSLETTER_SEARCH),
        {"otsi": "x"},
        headers={
            "HX-Current-URL": (
                "https://dash.orgusaar.ee/uudised/"
                "?periood=1a&kategooria=meie_uudised&sort=vaadatud&otsing=eksport"
            )
        },
    )

    pushed = response.headers["HX-Push-Url"]
    assert "periood=1a" in pushed
    assert "kategooria=meie_uudised" in pushed
    assert "sort=vaadatud" in pushed
    assert "otsing=eksport" in pushed


def test_the_newsletter_fragment_chips_keep_the_news_archive(viewer_client):
    """Not just the pushed URL — the chips rendered into the swapped region.

    They are rebuilt on every keystroke, so if they did not carry the archive's
    state a reader would lose it on the click *after* typing rather than on the
    keystroke itself.
    """
    send(1, "Kutse ärifoorumile")

    content = viewer_client.get(
        reverse(NEWSLETTER_SEARCH),
        {"otsi": "ärifoorum"},
        headers={"HX-Current-URL": "https://dash.orgusaar.ee/uudised/?periood=1a"},
    ).content.decode()

    # The `Tühjenda otsing` link is the one control this region renders that
    # navigates back to the page, so it is where the carried state shows.
    assert "periood=1a" in content


# -- the send archive's subject box ------------------------------------------


def test_the_archive_fragment_narrows_and_resets_the_page(viewer_client):
    send(1, "Kutse ärifoorumile")
    send(2, "Sündmuste kalender", newsletter="")

    response = viewer_client.get(reverse(ARCHIVE_SEARCH), {"otsi": "ärifoorum", "lk": "9"})
    content = response.content.decode()

    assert "Kutse ärifoorumile" in content
    assert "Sündmuste kalender" not in content
    assert "<html" not in content

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("news-newsletter-history"))
    assert "lk=" not in pushed


def test_the_archive_fragment_offers_to_clear_the_search_it_is_showing(viewer_client):
    """`Tühjenda` moved into the swapped region on purpose.

    Beside the box it would have gone on offering to clear a term the reader had
    already typed away, because that part of the page never swaps.
    """
    send(1, "Kutse ärifoorumile")

    with_term = viewer_client.get(reverse(ARCHIVE_SEARCH), {"otsi": "ärifoorum"})
    without = viewer_client.get(reverse(ARCHIVE_SEARCH))

    assert "Tühjenda otsing" in with_term.content.decode()
    assert "Tühjenda otsing" not in without.content.decode()


# -- both are ordinary protected routes --------------------------------------


@pytest.mark.parametrize("name", [NEWSLETTER_SEARCH, ARCHIVE_SEARCH])
def test_a_fragment_is_behind_the_viewer_gate(client, name):
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


@pytest.mark.parametrize("name", [NEWSLETTER_SEARCH, ARCHIVE_SEARCH])
def test_an_expired_session_redirects_rather_than_swapping_a_login_form(client, name):
    response = client.get(reverse(name), headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/sisene/?")
    assert b"PIN" not in response.content
