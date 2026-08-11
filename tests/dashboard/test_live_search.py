"""The address bar after a keystroke.

`apps.dashboard.live_search` is what keeps a live search reloadable: the results
swap in without a navigation, so unless the URL is rewritten server-side the
reader ends up with a page whose address describes a search they are no longer
looking at.

Two of these are security assertions rather than behaviour ones. `HX-Current-URL`
is an ordinary client header, and its value comes back out in `HX-Push-Url` —
which the browser puts in its own address bar. Nothing from it may reach the
path, and nothing the page has not declared may reach the query.
"""

from django.test import RequestFactory

from apps.dashboard.live_search import MAX_QUERY_LENGTH, carried_query, push_url

ALLOWED = ("periood", "sisu", "otsi")


def request_from(current_url=None):
    headers = {"HTTP_HX_CURRENT_URL": current_url} if current_url else {}
    return RequestFactory().get("/nahtavus/otsi/uudiskirjad/", **headers)


def test_the_rest_of_the_page_survives_a_keystroke():
    """The failure this exists for.

    The Nähtavus page carries a period, a content section and two searches. A
    keystroke sends only its own form, so a URL built from that alone would drop
    the other three — invisibly, until the reader reloaded and found the page
    back on its defaults.
    """
    request = request_from("https://dash.orgusaar.ee/nahtavus/?periood=koik&sisu=uudised")

    pushed = push_url(
        request,
        path="/nahtavus/",
        allowed=ALLOWED,
        updates={"otsi": "ärifoorum"},
        anchor="#section-newsletter-analytics",
    )

    assert pushed.startswith("/nahtavus/?")
    assert "periood=koik" in pushed
    assert "sisu=uudised" in pushed
    assert "otsi=%C3%A4rifoorum" in pushed
    assert pushed.endswith("#section-newsletter-analytics")


def test_an_emptied_box_drops_its_parameter_rather_than_pushing_a_blank():
    request = request_from("https://dash.orgusaar.ee/nahtavus/?periood=koik&otsi=vana")

    pushed = push_url(request, path="/nahtavus/", allowed=ALLOWED, updates={"otsi": ""})

    assert "otsi" not in pushed
    assert "periood=koik" in pushed


def test_the_pushed_path_never_comes_from_the_header():
    """A URL echoed into `HX-Push-Url` is a URL the browser trusts.

    Only the query is taken from `HX-Current-URL`; the path is always the
    caller's own `reverse()`. Without that, a crafted header would decide what
    address the reader's browser displayed for the page they are on.
    """
    request = request_from("https://evil.example/phish/?periood=koik")

    pushed = push_url(request, path="/nahtavus/", allowed=ALLOWED, updates={"otsi": "x"})

    assert pushed.startswith("/nahtavus/?")
    assert "evil.example" not in pushed
    assert "phish" not in pushed


def test_undeclared_parameters_are_dropped_rather_than_echoed():
    request = request_from("https://dash.orgusaar.ee/nahtavus/?periood=koik&utm_source=spam&x=1")

    carried = carried_query(request, ALLOWED)

    assert set(carried) == {"periood"}


def test_an_oversized_query_is_discarded_whole():
    long_query = "&".join(f"periood=x{index}" for index in range(MAX_QUERY_LENGTH))
    request = request_from(f"https://dash.orgusaar.ee/nahtavus/?{long_query}")

    assert list(carried_query(request, ALLOWED)) == []


def test_no_header_is_not_an_error():
    """A fragment can be requested without htmx having sent the header."""
    pushed = push_url(request_from(), path="/uudised/", allowed=ALLOWED, updates={"otsi": "eelnõu"})

    assert pushed == "/uudised/?otsi=eeln%C3%B5u"


def test_a_search_with_nothing_carried_and_nothing_typed_is_the_bare_page():
    pushed = push_url(request_from(), path="/uudised/", allowed=ALLOWED, updates={"otsi": ""})

    assert pushed == "/uudised/"
