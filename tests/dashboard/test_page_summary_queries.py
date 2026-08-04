"""Each page reads every source it needs exactly once.

Every module page loads its own feed summary for its content, and the shell's
freshness row needs the summaries of all four wired modules. Before this was
addressed the page loaded its own summary twice: once for the page, and again
inside `current_freshness()`.

These count selector calls rather than raw queries. A raw query count would also
move whenever unrelated page content changed, and would tell a future reader
nothing about *which* read was redundant.
"""

from __future__ import annotations

import importlib
from collections import Counter

import pytest
from django.urls import reverse

from apps.dashboard import freshness

pytestmark = pytest.mark.django_db

# Each wired module, in the order of `freshness._SUMMARY_SOURCES`, with the
# selector name and every module-level binding of it. A view does
# `from .selectors import get_x_summary`, so the view's own binding has to be
# replaced as well as the registry — otherwise the page's own read goes
# uncounted and a duplicate would hide.
_MODULES = (
    ("legal_work", "get_legal_work_summary", ("apps.legal_work.views", "apps.dashboard.views")),
    ("membership", "get_membership_summary", ("apps.membership.views", "apps.dashboard.views")),
    ("news", "get_news_summary", ("apps.news.views", "apps.dashboard.views")),
    ("events", "get_event_summary", ("apps.events.views", "apps.dashboard.views")),
)

ALL_MODULES = {name for name, _attribute, _bindings in _MODULES}

# Each routed page that loads a feed summary of its own, and which one.
# `visibility` is deliberately absent: it renders the freshness row but loads no
# feed summary, so it has nothing to reuse.
PAGES_WITH_OWN_SUMMARY = [
    ("legal-work", "legal_work"),
    ("events", "events"),
    ("news", "news"),
    ("membership", "membership"),
]


@pytest.fixture
def count_summary_reads(monkeypatch):
    """Count how often each module's summary selector actually runs.

    Every binding of a given selector is replaced by the *same* counting
    wrapper, so it does not matter which path a view takes to reach it.
    """
    calls: Counter[str] = Counter()
    registry = freshness._SUMMARY_SOURCES

    def make_counter(name, loader):
        def counting():
            calls[name] += 1
            return loader()

        return counting

    wrappers = {
        name: make_counter(name, loader)
        for (name, _attribute, _bindings), (_summary_class, loader) in zip(
            _MODULES, registry, strict=True
        )
    }

    monkeypatch.setattr(
        freshness,
        "_SUMMARY_SOURCES",
        tuple(
            (summary_class, wrappers[name])
            for (name, _attribute, _bindings), (summary_class, _loader) in zip(
                _MODULES, registry, strict=True
            )
        ),
    )

    for name, attribute, bindings in _MODULES:
        for module_path in bindings:
            module = importlib.import_module(module_path)
            if hasattr(module, attribute):
                monkeypatch.setattr(module, attribute, wrappers[name])

    return calls


@pytest.fixture
def viewer(client, authenticate_viewer):
    authenticate_viewer(client)
    return client


@pytest.mark.parametrize(("route", "module"), PAGES_WITH_OWN_SUMMARY)
def test_a_module_page_reads_its_own_summary_once(viewer, count_summary_reads, route, module):
    """Regression: the page loaded its own summary, then loaded it a second
    time inside the shell freshness row."""
    response = viewer.get(reverse(route))

    assert response.status_code == 200
    assert count_summary_reads[module] == 1, (
        f"{route} read the {module} summary {count_summary_reads[module]} times"
    )


@pytest.mark.parametrize(("route", "module"), PAGES_WITH_OWN_SUMMARY)
def test_a_module_page_still_reads_every_other_summary_once(
    viewer, count_summary_reads, route, module
):
    """The freshness row speaks for all four modules, so reusing one summary
    must not stop the other three from being read."""
    viewer.get(reverse(route))

    assert dict(count_summary_reads) == dict.fromkeys(ALL_MODULES, 1)


def test_the_overview_reads_each_summary_once(viewer, count_summary_reads):
    """It reads all four for its own content and hands all four back."""
    response = viewer.get(reverse("home"))

    assert response.status_code == 200
    assert dict(count_summary_reads) == dict.fromkeys(ALL_MODULES, 1)


def test_the_visibility_page_reads_each_summary_once(viewer, count_summary_reads):
    """It loads no feed summary of its own, so there is nothing to reuse — and
    nothing to double up on either."""
    viewer.get(reverse("visibility"))

    assert dict(count_summary_reads) == dict.fromkeys(ALL_MODULES, 1)


def test_the_htmx_fragment_still_loads_every_summary_itself(viewer, count_summary_reads):
    """The standalone endpoint has no page content to borrow from, so reading
    all four is its whole job."""
    response = viewer.get("/dashboard/varskus/", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert dict(count_summary_reads) == dict.fromkeys(ALL_MODULES, 1)


def test_a_caller_may_supply_no_summary_at_all():
    """`current_freshness()` with nothing preloaded still describes every
    module, which is what the fragment and the visibility page rely on."""
    state = freshness.current_freshness()

    assert state.total_sources == len(_MODULES)


def test_supplying_one_summary_does_not_change_what_the_row_reports(viewer):
    """Reuse is a query optimisation, never a change of meaning."""
    from apps.legal_work.selectors import get_legal_work_summary

    without = freshness.current_freshness()
    with_preloaded = freshness.current_freshness(get_legal_work_summary())

    assert with_preloaded.connected_sources == without.connected_sources
    assert with_preloaded.total_sources == without.total_sources
    assert with_preloaded.stale_sources == without.stale_sources
