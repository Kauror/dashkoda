"""Shared fixtures for the news tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def viewer_client(client, authenticate_viewer):
    """A PIN-authenticated viewer with no Django account at all.

    The same fixture the membership and visibility suites define, for the same
    reason: every routed page is behind the viewer gate, so a test that fetched
    a page without it would assert against a redirect.
    """
    authenticate_viewer(client)
    return client
