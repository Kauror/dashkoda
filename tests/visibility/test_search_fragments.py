"""The live-search fragment on the Koduleht page.

It answers a keystroke with the results region and nothing else. What this holds
is the contract that makes that safe to ship:

- the fragment is a *fragment* — no shell, no navigation, no second `<h1>`;
- it narrows by the same term the form would have submitted, and by the same
  filters, so typing and submitting cannot disagree;
- it resets pagination, because a new term is a new question and page 40 of a
  four-row result does not exist;
- it is behind the viewer gate like every other route, and an expired session
  gets `HX-Redirect` rather than a login form swapped into a table.

There were three fragments here. The newsletter sends box and the send archive's
subject box went to `tests/news/test_newsletter_fragments.py` with the routes
they answer: both push a `/uudised/` URL now, which is the one thing a fragment
test on this page could not assert.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

TRAFFIC_SEARCH = "visibility-traffic-search"


# -- the website-pages box ---------------------------------------------------


def test_the_traffic_fragment_is_a_fragment_and_resets_the_page(viewer_client):
    response = viewer_client.get(reverse(TRAFFIC_SEARCH), {"otsing": "liikmemaks", "lk": "4"})
    content = response.content.decode()

    assert "<html" not in content
    assert "<form" not in content
    # `lk` neither reaches the builder nor the address bar: a new term is a new
    # question, and page 4 of a two-row result answers "nothing found".
    assert "lk=" not in response.headers["HX-Push-Url"]
    assert response.headers["HX-Push-Url"].endswith("#section-otsing")


def test_the_traffic_fragment_pushes_the_page_it_belongs_to(viewer_client):
    """Koduleht keeps its own fragment, and its own URL state.

    Stated explicitly because the other two fragments moved: what makes this one
    a visibility route is that the address it puts in the reader's bar is the
    Koduleht page's — `/koduleht/`, in the view that answers a page search.
    """
    pushed = viewer_client.get(reverse(TRAFFIC_SEARCH), {"otsing": "liikmemaks"}).headers[
        "HX-Push-Url"
    ]

    assert pushed.startswith(reverse("visibility"))
    assert "fookus=lehed" in pushed


# -- it is an ordinary protected route ---------------------------------------


def test_a_fragment_is_behind_the_viewer_gate(client):
    response = client.get(reverse(TRAFFIC_SEARCH))

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_an_expired_session_redirects_rather_than_swapping_a_login_form(client):
    response = client.get(reverse(TRAFFIC_SEARCH), headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/sisene/?")
    assert b"PIN" not in response.content
