import html as html_module
import re

import pytest
from django.utils.html import strip_tags

from apps.dashboard.navigation import NAVIGATION, iter_items, parent_key

pytestmark = pytest.mark.django_db

#: The heading whose digits are a constant rather than a measurement. It names
#: the section's horizon, which is the one fact a reader needs to know whether
#: an empty list means a quiet fortnight or a quiet year.
TIMELINE_HEADING = "Järgmised 30 päeva"

#: The sections a reader always sees, in order. `Tähelepanu` is deliberately not
#: here: it renders only when a domain raised a signal, and with an empty
#: database none can — `test_the_attention_section_is_silent_rather_than_empty`
#: is the other half of that rule.
#:
#: `Andmete seis` moved to `/haldus/` on 2026-08-15 and does not come back;
#: `tests/dashboard/test_admin_area.py` proves it arrived.
SECTION_TITLES = [
    "Põhinäitajad",
    TIMELINE_HEADING,
    "Praegu enim huvi",
    "Auditooriumid",
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
    # Every module in the sidebar is routed, parents and children alike. The
    # inert-item rule still exists — `is_available` decides it, and
    # `test_components` covers the rendering — but nothing waits behind it.
    assert {item.key for item in routed} == {
        "overview",
        "membership",
        "legislation",
        "events",
        "visibility",
        "news",
        "shop",
        "mailings",
    }
    # Arvamused, Projektid, Finantsid and Fookusteemad were all removed at the
    # board's request rather than left as names the sidebar cannot open.
    assert planned == []
    assert 'aria-disabled="true"' not in content


def test_the_primary_navigation_is_five_items_in_order(client, authenticate_viewer):
    """The order the board reads, top level only.

    Written out rather than derived, because the point of the assertion is that
    somebody has to change this list deliberately.
    """
    assert [item.label for item in NAVIGATION] == [
        "Ülevaade",
        "Liikmeskond",
        "Õigusloome",
        "Sündmused",
        "Koduleht",
    ]


def test_koduleht_carries_exactly_three_children(client, authenticate_viewer):
    """The three facets of the Chamber's public surface, in order.

    Nesting is information architecture and nothing else: these are three
    separately routed pages with three separate bodies of code, and sharing a
    menu parent joins none of it.
    """
    koduleht = next(item for item in NAVIGATION if item.key == "visibility")

    assert [child.label for child in koduleht.children] == [
        "Uudised",
        "E-pood",
        "Otsepostitused",
    ]
    # The parent is a page in its own right, not a folder.
    assert koduleht.url_name == "visibility"


def test_every_child_names_koduleht_as_its_parent():
    assert parent_key("news") == "visibility"
    assert parent_key("shop") == "visibility"
    assert parent_key("mailings") == "visibility"
    # A top-level entry has no parent, and neither does something unknown.
    assert parent_key("membership") == ""
    assert parent_key("nothing-like-this") == ""


def test_the_sidebar_nests_the_children_under_koduleht(client, authenticate_viewer):
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "dk-nav-sublist" in content
    assert "Fookusteemad" not in content


#: One navigation anchor and the text inside it. The shell renders the whole
#: menu three times — the desktop sidebar, the mobile drawer and the
#: `<noscript>` fallback — so a document-wide count of `aria-current` says
#: nothing. What matters is which anchor carries it, which is asked per link.
NAV_ANCHOR = re.compile(r"<a\s[^>]*>\s*([^<]*?)\s*</a>", re.S)


def anchors_for(content: str, label: str) -> list[str]:
    """Every navigation anchor whose visible text is exactly `label`."""
    return [match.group(0) for match in NAV_ANCHOR.finditer(content) if match.group(1) == label]


@pytest.mark.parametrize(
    ("url", "label"),
    [("/uudised/", "Uudised"), ("/epood/", "E-pood"), ("/otsepostitused/", "Otsepostitused")],
)
def test_a_child_page_marks_itself_current_and_its_parent_as_ancestor(
    client, authenticate_viewer, url, label
):
    """Exactly one entry is current, and the parent is recognisable without claiming to be it.

    `aria-current="page"` on both would tell a screen-reader user they are in two
    places at once, so the parent gets a quieter class and no ARIA state.
    """
    authenticate_viewer(client)

    content = client.get(url).content.decode()

    children = anchors_for(content, label)
    parents = anchors_for(content, "Koduleht")
    assert children and parents

    for anchor in children:
        assert 'aria-current="page"' in anchor
        assert "dk-nav-item-active" in anchor
    for anchor in parents:
        assert "dk-nav-item-ancestor" in anchor
        assert "aria-current" not in anchor
        # Still an ordinary link: Koduleht is a page that has children, not a
        # folder that only groups them.
        assert 'href="/koduleht/"' in anchor


def test_koduleht_itself_is_current_rather_than_an_ancestor(client, authenticate_viewer):
    """Opening the parent marks the parent, and marks no child."""
    authenticate_viewer(client)

    content = client.get("/koduleht/").content.decode()

    parents = anchors_for(content, "Koduleht")
    assert parents
    for anchor in parents:
        assert 'aria-current="page"' in anchor
        assert "dk-nav-item-ancestor" not in anchor
    for label in ("Uudised", "E-pood", "Otsepostitused"):
        for anchor in anchors_for(content, label):
            assert "aria-current" not in anchor


def test_overview_renders_no_fabricated_numbers(client, authenticate_viewer):
    """An empty database produces no business figure anywhere on the page.

    One carve-out, and it is a constant rather than a measurement: the heading
    `Järgmised 30 päeva` names the section's horizon. The section was called
    `Eesolevad tegevused` between 2026-08-16 and 2026-08-17 and said nothing at
    all about how far ahead it looked, which is worse than a digit in a title.

    Everything else is scanned: not one digit, anywhere.

    The invariant itself is stated where it belongs — every card unavailable and
    saying so, rather than showing a nought that would claim somebody counted no
    members.
    """
    authenticate_viewer(client)

    response = client.get("/")
    content = response.content.decode()
    page = response.context["page"]

    assert page.cards, "the page still describes its domains with no data"
    assert not any(card.is_available for card in page.cards)
    assert not page.signals, "no source can support a signal"
    assert not page.upcoming
    assert not page.available_interest
    assert not page.has_any_source

    visible_text = html_module.unescape(strip_tags(content))
    visible_text = visible_text.replace(TIMELINE_HEADING, "")
    assert re.search(r"\d", visible_text) is None, visible_text

    assert "Andmeallikas ei ole ühendatud." in content
    # The audience strip words it differently: those figures are entered by
    # hand, so nobody has failed to connect anything — nobody has typed one in
    # yet.
    assert "Andmed puuduvad." in content


def test_the_attention_section_is_silent_rather_than_empty(client, authenticate_viewer):
    """With nothing flagged, `Tähelepanu` is absent rather than reassuring.

    Silence is a real answer and is not worth a section header plus a line
    saying nothing happened. A reader who learns to skim this section when it is
    full of routine will skim it on the day it is not.
    """
    authenticate_viewer(client)

    response = client.get("/")
    content = response.content.decode()

    assert not response.context["page"].signals
    assert "Tähelepanu" not in content
    assert 'aria-labelledby="tahelepanu"' not in content


def test_the_six_domain_cards_are_named_even_with_no_data(client, authenticate_viewer):
    """Every domain is described, and every one says its source is missing.

    The card is the page's structure and does not appear only once a source is
    connected: a domain silently absent from the front page reads as a domain
    that does not exist.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    for label in (
        "Liikmeskond",
        "Õigusloome",
        "Sündmused",
        "Koduleht ja uudised",
        "Otsepostitused",
        "E-pood",
    ):
        assert label in content
    assert content.count("Andmeallikas ei ole ühendatud.") == 6


def test_the_overview_no_longer_carries_the_tall_pillar_card(client, authenticate_viewer):
    """`Koja seis` and its two pillars were replaced on 2026-08-17.

    The component is gone rather than unused: a template nothing includes is a
    second definition of a card waiting to be picked up by mistake.
    """
    from django.template import TemplateDoesNotExist
    from django.template.loader import get_template

    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Koja seis" not in content
    with pytest.raises(TemplateDoesNotExist):
        get_template("dashboard/components/executive_pillar.html")


def test_the_overview_no_longer_reproduces_the_legal_topic_lists(client, authenticate_viewer):
    """Half of `/oigusloome/` sat a scroll above the link to it until 2026-08-17."""
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    assert "Viimased välja saadetud" not in content
    assert 'aria-labelledby="oigusloome"' not in content


def test_the_audience_strip_names_every_channel_and_says_which_are_empty(
    client, authenticate_viewer
):
    """Five audiences, and with nothing entered, no figures.

    Each has somewhere to store a value — `apps.visibility` — but a database
    with no observation in it is exactly the state a fresh deployment is in, and
    the strip must say so rather than imply a zero.

    **The website is not among them.** Its sessions are the `Koduleht ja
    uudised` card's headline, and one measure under two labels on one page
    invites a reconciliation nobody can perform; `build_channel_band` leaves the
    slot out at the source, so the query does not run either.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()

    for label in (
        "Uudiskirjad",
        "Facebooki jälgijad",
        "LinkedIni jälgijad",
        "Instagrami jälgijad",
        "YouTube’i tellijad",
    ):
        assert label in content
    assert "Kodulehe külastused" not in content
    assert "Google Analytics ei ole ühendatud." not in content
    assert "Andmed puuduvad." in content
    # Press coverage and the newsletter are named on the Uudised page, which is
    # where they are promised. The overview's news card no longer carries a
    # footer strip listing them as unconnected.
    assert "Meediakajastused" not in content


def test_the_audience_strip_is_no_longer_a_grid_of_large_cards(client, authenticate_viewer):
    """Six `channel_card` articles were the largest section of the front page.

    They said less than any other section and sat below the operational
    intelligence, so they are compact rows now. The component itself is
    untouched — Otsepostitused still renders the newsletter card with it — and
    both read the same `ChannelSlot`, so neither can describe a figure
    differently from the other.
    """
    authenticate_viewer(client)

    content = client.get("/").content.decode()
    strip = content.split('aria-labelledby="section-channels"', 1)[1]

    assert "text-metric" not in strip, "the large KPI figure size belongs to a card"
    assert "Auditooriumid" in content


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
