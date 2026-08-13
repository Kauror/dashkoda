"""The news archive's live-search fragment.

The one thing here that no other fragment does is the out-of-band swap: the
count sits above the archive card and the rows sit inside it, so one response
has to update two places. If the `hx-swap-oob` element ever stops being emitted,
the rows filter and the count above them goes on describing the previous search
— which is worse than not filtering at all, because it reads as a real answer.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

SEARCH = "news-search"


def test_the_fragment_is_a_fragment(viewer_client):
    content = viewer_client.get(reverse(SEARCH)).content.decode()

    for shell in ("<html", "<body", "Peamenüü"):
        assert shell not in content
    # The box stays on the page; only the rows come back.
    assert 'type="search"' not in content


def test_the_count_rides_along_as_an_out_of_band_swap(viewer_client):
    content = viewer_client.get(reverse(SEARCH), {"otsing": "eelnõu"}).content.decode()

    assert 'id="news-summary"' in content
    assert 'hx-swap-oob="true"' in content


def test_the_fragment_resets_the_page_and_keeps_the_period(viewer_client):
    response = viewer_client.get(
        reverse(SEARCH),
        {"otsing": "eelnõu", "lk": "7"},
        headers={"HX-Current-URL": "https://dash.orgusaar.ee/uudised/?periood=kvartal&lk=7"},
    )

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("news"))
    assert "periood=kvartal" in pushed
    assert "otsing=eeln%C3%B5u" in pushed
    # A new term is a new question: page 7 of a six-row result does not exist.
    assert "lk=" not in pushed


def test_the_fragment_keeps_the_newsletter_the_reader_chose(viewer_client):
    """The newsletter section shares this page and is not in this form.

    Its two parameters reach the pushed URL from `HX-Current-URL`, so typing an
    article title must not silently clear the newsletter and the subject search
    the reader set below.
    """
    response = viewer_client.get(
        reverse(SEARCH),
        {"otsing": "eelnõu"},
        headers={
            "HX-Current-URL": (
                "https://dash.orgusaar.ee/uudised/?uudiskiri=newsletter_enews&otsi=aastakoosolek"
            )
        },
    )

    pushed = response.headers["HX-Push-Url"]
    assert "uudiskiri=newsletter_enews" in pushed
    assert "otsi=aastakoosolek" in pushed


def test_the_fragment_is_behind_the_viewer_gate(client):
    response = client.get(reverse(SEARCH))

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")
