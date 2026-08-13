import html as html_module
import re

import pytest
from django.utils.html import strip_tags

from apps.dashboard.navigation import NAVIGATION, iter_items

pytestmark = pytest.mark.django_db

SECTION_TITLES = [
    "Põhinäitajad",
    "Õigusloome",
    "Liikmeskond",
    "Tulevased sündmused",
    "Viimased uudised",
    "Kanalite statistika",
]


def test_overview_requires_viewer_access(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.url.startswith("/sisene/?")


def test_overview_renders_the_shell(client, authenticate_viewer):
    authenticate_viewer(client)

    response = client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert content.count("<h1") == 1
    assert "Koja töölaud" in content
    assert 'href="#main"' in content
    assert 'id="main"' in content


@pytest.mark.parametrize("title", SECTION_TITLES)
def test_overview_contains_every_required_section(client, authenticate_viewer, title):
    authenticate_viewer(client)

    assert title in client.get("/").content.decode()


def test_navigation_routes_only_the_implemented_modules(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    entries = list(iter_items())
    routed = [item for item in entries if item.is_available]
    planned = [item for item in entries if not item.is_available]

    for item in entries:
        assert item.label in content
    assert 'aria-current="page"' in content
    # Every module in the sidebar is routed. The inert-item rule still exists —
    # `is_available` decides it, and `test_components` covers the rendering —
    # but nothing is waiting behind it any more.
    assert {item.key for item in routed} == {
        "overview",
        "membership",
        "legislation",
        "events",
        "news",
        "visibility",
        "shop",
    }
    # Arvamused, Projektid, Finantsid and Fookusteemad were all removed at the
    # board's request rather than left as names the sidebar cannot open.
    assert planned == []
    assert 'aria-disabled="true"' not in content


def test_the_sidebar_names_no_module_it_cannot_open(client, authenticate_viewer):
    """No nesting and no `Lisamisel` chip: every entry is a working link."""
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert [item for item in NAVIGATION if item.children] == []
    assert "dk-nav-sublist" not in content
    assert "Fookusteemad" not in content


def test_overview_renders_no_fabricated_numbers(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    # The whole page, with nothing cut out of it. This used to strip the
    # connection-state strip first, because the check time it printed was a fact
    # about the application rather than business data; that strip is gone, so
    # the page must now be free of digits outright.
    #
    # Entities are decoded before the scan. `strip_tags` leaves `&#x27;` intact,
    # and the digits inside a numeric entity would otherwise read as a passing
    # page while a label such as "YouTube'i" was quietly contributing "27".
    visible_text = html_module.unescape(strip_tags(content))

    assert re.search(r"\d", visible_text) is None, visible_text
    assert "Andmeallikas ei ole veel ühendatud." in content
    # The channel band words it differently: those figures are entered by hand,
    # so nobody has failed to connect anything — nobody has typed one in yet.
    assert "Andmed puuduvad." in content


def test_overview_names_every_channel_and_says_which_are_empty(client, authenticate_viewer):
    """The band shows all six channels and, with nothing entered, no figures.

    Five of the six now have somewhere to store a value — `apps.visibility` — but
    a database with no observation in it is exactly the state a fresh deployment
    is in, and the band must say so rather than imply a zero. Website visits stay
    unconnected regardless: nothing collects them at all.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    for label in (
        "Kodulehe külastused",
        "Uudiskirjad",
        "Facebooki jälgijad",
        "LinkedIni jälgijad",
        "Instagrami jälgijad",
        "YouTube’i tellijad",
    ):
        assert label in content
    assert "Google Analytics ei ole ühendatud." in content
    assert "Andmed puuduvad." in content
    # Press coverage and the newsletter are named on the Uudised page, which is
    # where they are promised. The overview's news card no longer carries a
    # footer strip listing them as unconnected.
    assert "Meediakajastused" not in content


def test_overview_keeps_logout_as_a_csrf_protected_post(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert 'action="/logi-valja/"' in content
    assert 'method="post"' in content
    assert "csrfmiddlewaretoken" in content


def test_overview_renders_one_logout_control_per_layout(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    # Exactly three logout forms exist: the mobile top bar, the desktop sidebar
    # and the drawer's copy of that sidebar. Each layout reveals only its own,
    # so a viewer never sees two. The desktop header must not add a fourth.
    # Which of the three is on screen at a given width is asserted by the
    # browser suite, because it depends on CSS.
    assert content.count('action="/logi-valja/"') == 3
    assert content.count("dk-sidebar") == 2


def test_overview_loads_only_local_bundled_assets(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert '<link rel="stylesheet" href="/static/build/styles.css">' in content
    assert '<script type="module" src="/static/build/app.js"></script>' in content
    assert "<script>" not in content
    assert 'style="' not in content
    # Every asset the page *loads* is local. Checked as asset URLs rather than
    # as a blanket "no https anywhere", because the channel band legitimately
    # links out to the Chamber's public social pages once a figure is entered —
    # a link a reader may follow is not an asset the page pulls in.
    assets = re.findall(r'<(?:script|link|img)\b[^>]*\b(?:src|href)="([^"]+)"', content)
    assert assets, "the page is expected to load its own bundle"
    assert all(url.startswith("/static/") for url in assets), assets


def test_overview_carries_no_connection_state_strip(client, authenticate_viewer):
    """The strip and its refresh control were removed on 2026-08-11.

    It reported an operational fact — how many wired feeds publish, whether any
    is stale — to a board that cannot act on it, and its count had been 4/4
    continuously.

    The fragment behind it is deliberately still served: `/dashboard/varskus/`
    has its own tests in `test_freshness_fragment.py`, and `freshness.py` keeps
    the invariant that the denominator is four. What this test holds is that no
    page reaches for it any more — including the polling variant, which never
    existed and must not arrive by way of putting the strip back.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "freshness-region" not in content
    assert "Kontrolli uuesti" not in content
    assert "hx-get" not in content
    assert 'hx-trigger="every' not in content
