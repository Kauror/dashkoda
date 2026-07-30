"""Shared fixtures for the internal membership tests.

Everything is synthetic. The approved package is never committed and never read
by a test; `package_factory` invents its own coherent one.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.membership.bootstrap import ensure_internal_membership_source
from apps.membership.history_import import import_history_package

from .package_factory import build_package


@pytest.fixture
def internal_source(db):
    return ensure_internal_membership_source()


@pytest.fixture
def package_path(tmp_path):
    """A valid synthetic package on disk."""
    return build_package(tmp_path / "package.zip")


@pytest.fixture
def imported_package(db, package_path):
    """The synthetic package, actually imported."""
    return import_history_package(package_path, dry_run=False)


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="staff",
        password="synthetic-test-password",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def staff_client(client, staff_user, authenticate_viewer):
    """A client that has passed *both* gates: viewer PIN and staff login.

    Both are required to reach `/admin/`, so a test that forgets one would pass
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
