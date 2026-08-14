"""The programme page's live filter fragment.

This one is not just a search box: the whole filter form is live, so a year, a
tag or a status filters the rows the same way typing does. A page where typing
is instant but choosing a month needs a button press has two rules, and readers
learn the slower one.

The fragment must not read the public Koda.ee calendar. That section sits below
the swapped region, no filter changes it, and loading it on every keystroke
would put a second feed's queries behind a search box.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

SEARCH = "event-programme-search"


def test_the_fragment_is_a_fragment(viewer_client):
    content = viewer_client.get(reverse(SEARCH)).content.decode()

    for shell in ("<html", "<body", "Peamenüü"):
        assert shell not in content
    # The filter form stays on the page: replacing it would take the caret out
    # of the search box and close any select mid-choice.
    assert "<form" not in content
    assert "<select" not in content


def test_the_fragment_leaves_the_public_calendar_alone(viewer_client):
    content = viewer_client.get(reverse(SEARCH)).content.decode()

    assert "Koda.ee avalik kalender" not in content


def test_every_filter_reaches_the_pushed_url_and_the_page_resets(viewer_client):
    response = viewer_client.get(
        reverse(SEARCH),
        {"q": "foorum", "year": "2026", "tag": "koolitus", "page": "6"},
    )

    pushed = response.headers["HX-Push-Url"]
    assert pushed.startswith(reverse("events"))
    assert "q=foorum" in pushed
    assert "year=2026" in pushed
    assert "tag=koolitus" in pushed
    # Narrowing the filters is a new question; page 6 of the narrowed set may
    # not exist, and carrying it would answer "no events match".
    assert "page=" not in pushed


def test_the_chosen_ordering_survives_a_keystroke():
    """The failure this exists for.

    The ordering is chosen by chips, which are links, so it lives in the URL and
    not in the form. Without a hidden `sort` field a keystroke sends a form with
    no ordering in it, and the rows fall back to chronological while the chip
    still reads `Vaadatuimad`.
    """
    from apps.event_programme.views import PROGRAMME_FIELDS, PROGRAMME_PARAMS

    assert "sort" in PROGRAMME_FIELDS

    template = "apps/event_programme/templates/event_programme/partials/_focus_register.html"
    with open(template, encoding="utf-8") as handle:
        markup = handle.read()

    assert '<input type="hidden" name="sort"' in markup
    # The focus is the same class of state and the same failure: without it a
    # keystroke pushes a URL with no `fookus`, and the next full page load opens
    # the overview with a filter applied and nothing on screen saying so.
    assert '<input type="hidden" name="fookus"' in markup
    assert "fookus" in PROGRAMME_PARAMS


def test_a_cleared_filter_leaves_the_address_bar(viewer_client):
    """Unlike the four search boxes, this form can *remove* a filter.

    So the pushed URL is built from what the form submitted rather than from the
    reader's current query — otherwise clearing `year` would filter the rows and
    leave `year=2026` in the address bar for the next reload to reapply.
    """
    response = viewer_client.get(
        reverse(SEARCH),
        {"q": "foorum"},
        headers={"HX-Current-URL": "https://dash.orgusaar.ee/sundmused/?year=2026&q=vana"},
    )

    pushed = response.headers["HX-Push-Url"]
    assert "q=foorum" in pushed
    assert "year=" not in pushed


def test_the_fragment_is_behind_the_viewer_gate(client):
    response = client.get(reverse(SEARCH))

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")
