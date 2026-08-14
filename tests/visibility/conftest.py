"""Shared fixtures for the visibility tests.

Every value is synthetic and obviously so. The Chamber's real follower counts
are not in this repository, are not in any fixture, and must never be committed:
the first real figures are typed by a staff user after deployment.

Dates are relative to today rather than fixed, because the form refuses a future
observation date and a hard-coded date would start failing on its own.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.visibility.manual import VisibilitySubmission, publish_submission

NEW_URL = "/admin/data-entry/visibility/new/"
LIST_URL = "/admin/data-entry/visibility/"
HUB_URL = "/admin/data-entry/"
PAGE_URL = "/koduleht/"
LEGACY_PAGE_URL = "/nahtavus/"


@pytest.fixture
def today() -> date:
    return timezone.localdate()


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="visibility-staff",
        password="synthetic-test-password",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def plain_user(db):
    """An authenticated Django user who is not staff."""
    return get_user_model().objects.create_user(
        username="visibility-reader",
        password="synthetic-test-password",
        is_staff=False,
    )


@pytest.fixture
def staff_client(client, staff_user, authenticate_viewer):
    """A client that has passed *both* gates: viewer PIN and staff login.

    Both are required to reach `/admin/`, so a test that forgot one would pass
    for the wrong reason.
    """
    authenticate_viewer(client)
    client.force_login(staff_user)
    return client


@pytest.fixture
def viewer_client(client, authenticate_viewer):
    """A PIN-authenticated viewer with no Django account at all."""
    authenticate_viewer(client)
    return client


@pytest.fixture
def nonstaff_client(client, plain_user, authenticate_viewer):
    """A viewer who also holds an ordinary, non-staff Django account."""
    authenticate_viewer(client)
    client.force_login(plain_user)
    return client


def form_data(*, observation_date: date | None = None, note: str = "", **metrics) -> dict:
    """A submission as the browser would send it.

    Metric keys are given without the `metric_` prefix for readability;
    `None` removes a field entirely, which is how a test says "left blank".
    """
    data = {
        "observation_date": (observation_date or timezone.localdate()).isoformat(),
        "note": note,
    }
    for key, value in metrics.items():
        if value is not None:
            data[f"metric_{key}"] = value
    return data


def preview(data: dict) -> dict:
    return {**data, "action": "preview"}


def confirm(data: dict) -> dict:
    return {**data, "action": "confirm"}


@pytest.fixture
def submit(db):
    """Publish a submission directly through the service, bypassing the form.

    Used by the selector and page tests, which are about what is *stored* rather
    than about how it got there.
    """

    def _submit(*, observation_date: date | None = None, note: str = "", actor=None, **metrics):
        return publish_submission(
            VisibilitySubmission(
                observation_date=observation_date or timezone.localdate(),
                values={key: value for key, value in metrics.items() if value is not None},
                note=note,
            ),
            actor=actor,
        )

    return _submit


@pytest.fixture
def days_ago(today):
    def _days_ago(count: int) -> date:
        return today - timedelta(days=count)

    return _days_ago


# ---------------------------------------------------------------------------
# GA4 history
# ---------------------------------------------------------------------------
#
# `test_ga4_selectors.py` grew its own copy of this before the Koduleht work
# needed one too. This is the shared version, and it carries the two fields that
# copy does not: engagement seconds, and the `has_channel_detail` flag that
# separates "no channel rows were asked for" from "no channels had traffic".


@pytest.fixture
def ga4_provenance(db):
    """The source, artifact and import run every synthetic GA4 day hangs off."""
    from apps.sources.services import build_import_run, register_external_reference
    from apps.visibility.bootstrap import ensure_ga4_source

    source = ensure_ga4_source()
    artifact = register_external_reference(
        source=source,
        external_reference="synthetic:koduleht",
        original_name="synthetic.json",
        mime_type="application/json",
        sha256="d" * 64,
        size_bytes=10,
    )
    run = build_import_run(
        artifact=artifact,
        importer_name="synthetic_koduleht_test",
        schema_version="2.0",
        dry_run=False,
    )
    return source, artifact, run


@pytest.fixture
def ga4_day(ga4_provenance):
    """Publish one synthetic reporting day.

    `pages` is `(path, views[, engagement_seconds[, active_users]])` and
    `channels` is `(name, sessions[, engaged_sessions])`. Both detail flags
    follow whether rows were given, which is the distinction the coverage object
    reads: a day with no page rows was not measured at zero pages.

    Pass `has_page_detail` or `has_channel_detail` explicitly to describe the day
    that *was* queried and genuinely returned nothing.
    """
    from django.utils import timezone as django_timezone

    from apps.visibility.models import Ga4ChannelDaily, Ga4DailySnapshot, Ga4PageDaily

    counter = {"n": 0}
    source, artifact, run = ga4_provenance

    def _day(
        report_date,
        *,
        current=True,
        pages=(),
        channels=(),
        has_page_detail=None,
        has_channel_detail=None,
        **figures,
    ):
        counter["n"] += 1
        snapshot = Ga4DailySnapshot.objects.create(
            source=source,
            artifact=artifact,
            import_run=run,
            report_date=report_date,
            observed_at=django_timezone.now(),
            checksum=f"{counter['n']:064d}",
            is_current_for_date=current,
            has_page_detail=bool(pages) if has_page_detail is None else has_page_detail,
            has_channel_detail=bool(channels) if has_channel_detail is None else has_channel_detail,
            **figures,
        )
        for path, views, *rest in pages:
            Ga4PageDaily.objects.create(
                snapshot=snapshot,
                report_date=report_date,
                path=path,
                page_views=views,
                user_engagement_seconds=rest[0] if rest else None,
                active_users=rest[1] if len(rest) > 1 else None,
            )
        for name, sessions, *rest in channels:
            Ga4ChannelDaily.objects.create(
                snapshot=snapshot,
                report_date=report_date,
                channel=name,
                sessions=sessions,
                engaged_sessions=rest[0] if rest else None,
            )
        return snapshot

    return _day
