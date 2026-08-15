"""Every address the newsletters have ever had, and where it lands now.

The Smaily material has moved twice: Nähtavus → Uudised → Otsepostitused. Each
move left real bookmarks behind, and one of the old addresses — the send
archive — has now been the canonical one under two different apps.

What this suite pins:

- **every retired address resolves**, and resolves to Otsepostitused;
- **nothing chains.** An old link costs one redirect, not two: the `/nahtavus/`
  routes were re-aimed at the final destination when it moved rather than left
  pointing at the intermediate one;
- **no loop is reachable** from any entry point, which is checked by following
  each redirect to completion rather than by reading the first `Location`;
- **query parameters survive exactly where they still mean something.** The send
  archive's `uudiskiri`, `otsi` and `lk` mean the same thing on the other side
  and are carried whole. A saved `/uudised/?fookus=uudiskirjad&periood=1a` is a
  different case: `periood` belonged to the *news archive* sharing that address,
  and passing it on would put a key into an address bar that the receiving page
  cannot read back.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.visibility.models import SmailyCampaign, VisibilityMetric

pytestmark = pytest.mark.django_db

ENEWS = VisibilityMetric.NEWSLETTER_ENEWS

#: Every retired address, and the route each must reach. The two `/uudised/`
#: fragment routes are internal rather than bookmarked and are kept because htmx
#: follows a redirect transparently — a cached page still holding the old
#: attribute would otherwise start answering 404 mid-keystroke.
RETIRED = [
    ("visibility-campaign-history", "mailings-history"),
    ("visibility-campaign-history-search", "mailings-history-search"),
    ("news-newsletter-history", "mailings-history"),
    ("news-newsletter-history-search", "mailings-history-search"),
    ("news-newsletter-search", "mailings-search"),
]


def send(campaign_id, name, *, newsletter=ENEWS, days_ago=1):
    return SmailyCampaign.objects.create(
        campaign_id=campaign_id,
        name=name,
        template_name="tpl",
        newsletter=newsletter,
        status="COMPLETED",
        completed_at=timezone.now() - dt.timedelta(days=days_ago),
    )


# ======================================================================
# Every retired address lands on Otsepostitused, in one hop
# ======================================================================


@pytest.mark.parametrize(("old", "new"), RETIRED)
def test_a_retired_address_redirects_to_its_new_home(viewer_client, old, new):
    response = viewer_client.get(reverse(old))

    assert response.status_code == 302
    assert response.url.rstrip("?") == reverse(new)


@pytest.mark.parametrize(("old", "new"), RETIRED)
def test_a_retired_address_costs_exactly_one_redirect(viewer_client, old, new):
    """No chaining through the intermediate address.

    `/nahtavus/uudiskirjad/` pointed at `/uudised/uudiskirjad/`, which is itself
    a redirect now. Left that way the oldest bookmark in the product would take
    two hops; re-aiming it is what keeps the chain one deep.
    """
    response = viewer_client.get(reverse(old), follow=True)

    assert response.status_code == 200
    assert len(response.redirect_chain) == 1


@pytest.mark.parametrize(("old", "_new"), RETIRED)
def test_a_retired_address_does_not_loop(viewer_client, old, _new):
    """Followed to completion rather than read off the first `Location`.

    A loop is the failure mode a single-hop assertion cannot see, and Django's
    test client raises rather than spinning forever, so following is the check.
    """
    response = viewer_client.get(reverse(old), follow=True)

    assert response.status_code == 200
    final = response.redirect_chain[-1][0]
    assert final.startswith("/otsepostitused/")


# ======================================================================
# The send archive keeps its question
# ======================================================================


def test_the_old_archive_url_keeps_newsletter_search_and_page(viewer_client):
    """A saved bookmark is not a 404, and not a reset either.

    `uudiskiri`, `otsi` and `lk` are exactly what an archive bookmark carries and
    mean the same thing on the other side, so dropping them would land the
    reader in fourteen unfiltered years.
    """
    response = viewer_client.get(
        reverse("visibility-campaign-history"),
        {"uudiskiri": str(ENEWS), "otsi": "aastakoosolek", "lk": "3"},
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("mailings-history"))
    assert f"uudiskiri={ENEWS}" in response.url
    assert "otsi=aastakoosolek" in response.url
    assert "lk=3" in response.url


def test_the_uudised_archive_url_keeps_its_question_too(viewer_client):
    response = viewer_client.get(
        reverse("news-newsletter-history"), {"uudiskiri": str(ENEWS), "otsi": "aastakoosolek"}
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("mailings-history"))
    assert f"uudiskiri={ENEWS}" in response.url
    assert "otsi=aastakoosolek" in response.url


def test_the_old_archive_search_url_still_answers(viewer_client):
    """htmx follows the redirect, so the fragment that answers is the new one."""
    send(1, "Kutse ärifoorumile")

    response = viewer_client.get(
        reverse("visibility-campaign-history-search"), {"otsi": "ärifoorum"}, follow=True
    )

    assert response.status_code == 200
    assert "Kutse ärifoorumile" in response.content.decode()


# ======================================================================
# The retired focus of Uudised
# ======================================================================


def test_the_newsletter_focus_redirects_to_otsepostitused(viewer_client):
    """`/uudised/?fookus=uudiskirjad` is a real address people saved.

    Letting it fall through `parse_focus` would resolve it to the news overview,
    which renders happily and tells the reader nothing about where the thing they
    asked for went. So the view intercepts the value before the parser sees it.
    """
    response = viewer_client.get(reverse("news"), {"fookus": "uudiskirjad"})

    assert response.status_code == 302
    assert response.url.rstrip("?") == reverse("mailings")


def test_the_newsletter_focus_carries_the_newsletter_and_the_subject_search(viewer_client):
    """The two parameters that mean the same thing on both sides."""
    response = viewer_client.get(
        reverse("news"),
        {"fookus": "uudiskirjad", "uudiskiri": str(ENEWS), "otsi": "aastakoosolek"},
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("mailings"))
    assert f"uudiskiri={ENEWS}" in response.url
    assert "otsi=aastakoosolek" in response.url


def test_the_newsletter_focus_drops_the_news_archives_parameters(viewer_client):
    """`periood` and `kategooria` described the article archive on that address.

    They are not valid on the page this is going to, and reflecting them into
    its URL would hand the reader an address whose own page ignores half of it.
    """
    response = viewer_client.get(
        reverse("news"),
        {
            "fookus": "uudiskirjad",
            "periood": "1a",
            "kategooria": "meie_uudised",
            "sort": "vaadatud",
            "otsing": "eksport",
        },
    )

    assert response.status_code == 302
    for key in ("periood=", "kategooria=", "sort=", "otsing=", "fookus="):
        assert key not in response.url


def test_the_retired_focus_does_not_loop(viewer_client):
    response = viewer_client.get(reverse("news"), {"fookus": "uudiskirjad"}, follow=True)

    assert response.status_code == 200
    assert len(response.redirect_chain) == 1
    assert response.redirect_chain[-1][0].startswith("/otsepostitused/")


def test_an_unknown_focus_still_fails_safely(viewer_client):
    """The retired focus is intercepted; every other unreadable one is not.

    A rotted bookmark should still show the news, and only the one value that
    genuinely moved gets a redirect.
    """
    response = viewer_client.get(reverse("news"), {"fookus": "zzz"})

    assert response.status_code == 200
    assert "Uudised" in response.content.decode()


# ======================================================================
# The destinations themselves redirect nowhere
# ======================================================================


@pytest.mark.parametrize("name", ["mailings", "mailings-history"])
def test_the_new_addresses_render_rather_than_redirect(viewer_client, name):
    """What makes every chain above provably one hop deep."""
    response = viewer_client.get(reverse(name))

    assert response.status_code == 200
