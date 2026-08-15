"""The two Otsepostitused live-search fragments.

They have followed their routes twice: from Nähtavus to Uudised, and now to
`/otsepostitused/`. The contract has survived both moves unchanged — a fragment
is a fragment, it narrows by the same term the form would submit, it resets
pagination, and it is behind the viewer gate — and what each move adds is an
assertion about the URL pushed, because pushing the previous section's address
is exactly the way this breaks.

Two tests were dropped rather than moved: the ones asserting that a keystroke
carried the *news archive's* period and category through `HX-Current-URL`.
Otsepostitused has no news archive beside it, `carried` is empty by
construction, and a test demanding those parameters survive would be pinning
behaviour the move deliberately removed. What replaces them is the assertion
below that no news parameter reaches the pushed URL at all.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import SmailyCampaign, VisibilityMetric

pytestmark = pytest.mark.django_db

NEWSLETTER_SEARCH = "mailings-search"
ARCHIVE_SEARCH = "mailings-history-search"

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


def test_the_newsletter_fragment_pushes_otsepostitused(viewer_client):
    """The point of the move, in one assertion.

    The fragment pushed `/nahtavus/` once and `/uudised/` after that. Left
    either way, a reader typing in the subject box would watch their address bar
    change to a page that no longer has one.
    """
    response = viewer_client.get(reverse(NEWSLETTER_SEARCH), {"otsi": "ärifoorum"})

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("mailings"))
    assert not pushed.startswith(reverse("news"))
    assert not pushed.startswith(reverse("visibility"))
    assert "otsi=%C3%A4rifoorum" in pushed
    assert pushed.endswith("#section-newsletter-analytics")


def test_the_newsletter_fragment_carries_no_news_state(viewer_client):
    """A news parameter on the current URL must not ride along.

    This section used to sit on `/uudised/` and carried the article archive's
    period, category and ordering so a keystroke could not reset them. There is
    no archive here, so those keys mean nothing on the page being pushed — and
    an address holding parameters the page never reads is an address that lies
    about what is on screen.
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
    for key in ("periood=", "kategooria=", "sort=", "otsing=", "fookus="):
        assert key not in pushed


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
    assert pushed.startswith(reverse("mailings-history"))
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
