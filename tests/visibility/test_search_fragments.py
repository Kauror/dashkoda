"""The three live-search fragments on the visibility pages.

Each answers a keystroke with the results region and nothing else. What these
hold is the contract that makes that safe to ship:

- the fragment is a *fragment* — no shell, no navigation, no second `<h1>`;
- it narrows by the same term the form would have submitted, and by the same
  filters, so typing and submitting cannot disagree;
- it resets pagination, because a new term is a new question and page 40 of a
  four-row result does not exist;
- it is behind the viewer gate like every other route, and an expired session
  gets `HX-Redirect` rather than a login form swapped into a table.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import SmailyCampaign, VisibilityMetric

pytestmark = pytest.mark.django_db

NEWSLETTER_SEARCH = "visibility-newsletter-search"
TRAFFIC_SEARCH = "visibility-traffic-search"
ARCHIVE_SEARCH = "visibility-campaign-history-search"

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


def test_the_newsletter_fragment_pushes_the_page_it_belongs_to(viewer_client):
    response = viewer_client.get(reverse(NEWSLETTER_SEARCH), {"otsi": "ärifoorum"})

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("visibility"))
    assert "otsi=%C3%A4rifoorum" in pushed
    assert pushed.endswith("#section-newsletter-analytics")


def test_the_newsletter_fragment_carries_the_rest_of_the_page(viewer_client):
    """The period the reader had set must survive their typing."""
    response = viewer_client.get(
        reverse(NEWSLETTER_SEARCH),
        {"otsi": "x"},
        headers={"HX-Current-URL": "https://dash.orgusaar.ee/nahtavus/?periood=koik&sisu=uudised"},
    )

    pushed = response.headers["HX-Push-Url"]
    assert "periood=koik" in pushed
    assert "sisu=uudised" in pushed


# -- the website-pages box ---------------------------------------------------


def test_the_traffic_fragment_is_a_fragment_and_resets_the_page(viewer_client):
    response = viewer_client.get(reverse(TRAFFIC_SEARCH), {"otsing": "liikmemaks", "lk": "4"})
    content = response.content.decode()

    assert "<html" not in content
    assert "<form" not in content
    # `lk` neither reaches the builder nor the address bar: a new term is a new
    # question, and page 4 of a two-row result answers "nothing found".
    assert "lk=" not in response.headers["HX-Push-Url"]
    assert response.headers["HX-Push-Url"].endswith("#section-traffic")


# -- the archive box ---------------------------------------------------------


def test_the_archive_fragment_narrows_and_resets_the_page(viewer_client):
    send(1, "Kutse ärifoorumile")
    send(2, "Sündmuste kalender", newsletter="")

    response = viewer_client.get(reverse(ARCHIVE_SEARCH), {"otsi": "ärifoorum", "lk": "9"})
    content = response.content.decode()

    assert "Kutse ärifoorumile" in content
    assert "Sündmuste kalender" not in content
    assert "<html" not in content

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("visibility-campaign-history"))
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


# -- every one of them is an ordinary protected route ------------------------


@pytest.mark.parametrize("name", [NEWSLETTER_SEARCH, TRAFFIC_SEARCH, ARCHIVE_SEARCH])
def test_a_fragment_is_behind_the_viewer_gate(client, name):
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


@pytest.mark.parametrize("name", [NEWSLETTER_SEARCH, TRAFFIC_SEARCH, ARCHIVE_SEARCH])
def test_an_expired_session_redirects_rather_than_swapping_a_login_form(client, name):
    response = client.get(reverse(name), headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/sisene/?")
    assert b"PIN" not in response.content
