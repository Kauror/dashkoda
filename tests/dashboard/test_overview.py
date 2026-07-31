import html as html_module
import re

import pytest
from django.utils.html import strip_tags

from apps.dashboard.navigation import NAVIGATION, iter_items

pytestmark = pytest.mark.django_db

SECTION_TITLES = [
    "Põhinäitajad",
    "Juhatuse tähelepanu",
    "Muutus viimase kuu jooksul",
    "Õigusloome",
    "Liikmeskond",
    "Tulevased sündmused",
    "Viimased uudised",
    "Kanalite statistika",
]

FRESHNESS_REGION = re.compile(
    r'<div id="freshness-region".*?</div>\s*</div>',
    re.DOTALL,
)


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
    assert "Ülevaade koja olulisematest näitajatest ja tegevustest" in content
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
    # The modules backed by a connected source are routed; every other one is
    # still inert, marked, and never rendered as a link. Nested entries follow
    # the same rule, so a planned child cannot become a route by accident.
    assert {item.key for item in routed} == {
        "overview",
        "membership",
        "legislation",
        "events",
        "news",
        "visibility",
    }
    assert {item.key for item in planned} == {
        "opinions",
        "finance",
        "focus-topics",
        "projects",
        "projects-active",
        "projects-finished",
    }
    assert content.count('aria-disabled="true"') >= len(planned)


def test_navigation_nests_planned_children_under_their_parent(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    parents = [item for item in NAVIGATION if item.children]

    assert {item.key for item in parents} == {"legislation", "projects"}
    assert "dk-nav-sublist" in content
    for parent in parents:
        for child in parent.children:
            assert child.label in content


def test_overview_renders_no_fabricated_numbers(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    # The freshness region carries the connection-check time, which is a fact
    # about the application rather than business data. Everything else on the
    # page must be free of numbers until a verified source exists.
    #
    # Entities are decoded before the scan. `strip_tags` leaves `&#x27;` intact,
    # and the digits inside a numeric entity would otherwise read as a passing
    # page while a label such as "YouTube'i" was quietly contributing "27".
    without_freshness = FRESHNESS_REGION.sub("", content)
    visible_text = html_module.unescape(strip_tags(without_freshness))

    assert FRESHNESS_REGION.search(content) is not None
    assert re.search(r"\d", visible_text) is None, visible_text
    assert "Andmeallikas ei ole veel ühendatud." in content
    assert "Kontrollitud andmed puuduvad." in content


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
        "Uudiskirja saajad",
        "Facebooki jälgijad",
        "LinkedIni jälgijad",
        "Instagrami jälgijad",
        "YouTube’i tellijad",
    ):
        assert label in content
    assert "Google Analytics ei ole ühendatud." in content
    assert "Andmed puuduvad." in content
    assert "Meediakajastused" in content
    assert "Uudiskiri" in content


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
    assert "https://" not in content
    assert "<script>" not in content
    assert 'style="' not in content


def test_overview_wires_the_htmx_freshness_pattern(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert 'hx-get="/dashboard/varskus/"' in content
    assert 'hx-target="#freshness-region"' in content
    assert 'hx-trigger="every' not in content
    # Without JavaScript the same control is an ordinary GET form to the page.
    assert 'action="/"' in content
