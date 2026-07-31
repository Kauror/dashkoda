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
PAGE_URL = "/nahtavus/"


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
