"""Shared fixtures for the news tests."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.news.categories import NewsCategory
from apps.news.public_models import NewsResource, TitleOrigin

#: The synthetic property's coverage. Wide enough that a thirty-day window can
#: sit wholly inside it and still leave room on both sides, which is what lets a
#: test place an article before, inside or after the measured period on purpose.
COVERAGE_START = dt.date(2026, 1, 1)
COVERAGE_END = dt.date(2026, 6, 30)


@pytest.fixture
def viewer_client(client, authenticate_viewer):
    """A PIN-authenticated viewer with no Django account at all.

    The same fixture the membership and visibility suites define, for the same
    reason: every routed page is behind the viewer gate, so a test that fetched
    a page without it would assert against a redirect.
    """
    authenticate_viewer(client)
    return client


def article(slug: str, *, published: dt.date | None, category: str = NewsCategory.CHAMBER):
    """One catalogued article, published on a given day at midday Tallinn.

    Midday rather than midnight so the timezone conversion the analytics do —
    Tallinn rather than UTC — is never the thing a test is accidentally
    measuring.
    """
    moment = None
    if published is not None:
        moment = timezone.make_aware(
            dt.datetime.combine(published, dt.time(12, 0)),
            timezone.get_current_timezone(),
        )
    return NewsResource.objects.create(
        canonical_url=f"https://www.koda.ee/et/uudised/{slug}",
        path=f"/et/uudised/{slug}",
        title=f"Uudis {slug}",
        published_at=moment,
        title_origin=TitleOrigin.FEED,
        category=category,
        last_seen_at=timezone.now(),
    )


def listing(path: str):
    """A section index, as the discovery crawl really records one: undated."""
    return NewsResource.objects.create(
        canonical_url=f"https://www.koda.ee{path}",
        path=path,
        title="Uudised",
        published_at=None,
        title_origin=TitleOrigin.PAGE,
        last_seen_at=timezone.now(),
    )


@pytest.fixture
def ga4():
    """Collected GA4 days, with page rows written only where asked.

    A day always gets a snapshot — that is what "collected" means — and a page
    with no row on a collected day was seen by nobody. `skip` leaves a day
    uncollected instead, which is the different thing the analytics have to tell
    apart from a measured zero.
    """
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source
    from apps.visibility.models import Ga4DailySnapshot, Ga4PageDaily

    state = {"n": 0}

    def _cover(
        start: dt.date = COVERAGE_START,
        end: dt.date = COVERAGE_END,
        *,
        views: dict[str, dict[dt.date, int]] | None = None,
        skip: set[dt.date] | None = None,
        site_views_per_day: int = 100,
    ):
        source = ensure_ga4_source()
        views = views or {}
        skip = skip or set()
        day = start
        while day <= end:
            if day in skip:
                day += dt.timedelta(days=1)
                continue
            state["n"] += 1
            artifact = register_external_reference(
                source=source,
                external_reference=f"synthetic:ga4:{state['n']}",
                original_name="synthetic.json",
                mime_type="application/json",
                sha256=f"{state['n']:064d}",
                size_bytes=10,
            )
            run = build_import_run(
                artifact=artifact,
                importer_name="synthetic_ga4",
                schema_version="2.0",
                dry_run=False,
            )
            today_rows = {path: by_day[day] for path, by_day in views.items() if day in by_day}
            snapshot = Ga4DailySnapshot.objects.create(
                source=source,
                artifact=artifact,
                import_run=run,
                report_date=day,
                observed_at=timezone.now(),
                checksum=f"{state['n']:064d}",
                is_current_for_date=True,
                has_page_detail=True,
                sessions=1,
                page_views=site_views_per_day,
            )
            for path, count in today_rows.items():
                Ga4PageDaily.objects.create(
                    snapshot=snapshot, report_date=day, path=path, page_views=count
                )
            day += dt.timedelta(days=1)

    return _cover


@pytest.fixture
def coverage():
    """GA4 coverage as the selectors report it, read after `ga4` has run."""
    from apps.visibility.ga4_selectors import get_coverage

    return get_coverage
