"""The Microsoft Graph route is retired, and stays retired.

These are deliberately negative tests. The Graph route was removed because it
never completed live acceptance and had already drifted behind the public
route; the risk now is that a future change quietly reintroduces a second,
unaccepted collection path or a credential-bearing dependency.

The positive behaviour of the surviving route — imports, unchanged detection,
dry runs, failure containment and URL secrecy — lives in `test_public_sync.py`.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest
from django.core.management import get_commands

REPO_ROOT = Path(__file__).resolve().parents[2]

# Settings that existed only to configure the Graph route.
RETIRED_SETTINGS = (
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CLIENT_SECRET",
    "MS_GRAPH_TIMEOUT_SECONDS",
    "MS_GRAPH_MAX_ATTEMPTS",
    "OIGUSLOOME_DRIVE_ID",
    "OIGUSLOOME_ITEM_ID",
)

RETIRED_COMMANDS = ("sync_oigusloome", "resolve_oigusloome_share")


# -- the code is gone ---------------------------------------------------


def test_the_graph_module_no_longer_exists():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("apps.legal_work.graph")


def test_the_graph_only_sync_service_is_gone():
    """`synchronize()` drove the Graph route; the public route has its own."""
    sync = importlib.import_module("apps.legal_work.sync")

    assert not hasattr(sync, "synchronize")


def test_the_shared_feed_plumbing_survived():
    """Removing the route must not take the feed's own primitives with it."""
    sync = importlib.import_module("apps.legal_work.sync")

    for name in (
        "WORKBOOK_FILENAME",
        "ADVISORY_LOCK_NAMESPACE",
        "SyncLocked",
        "SyncOutcome",
        "advisory_lock",
        "get_feed_state",
        "record_failure",
        "EXIT_LOCKED",
    ):
        assert hasattr(sync, name), f"{name} is still needed by the public route"


# -- the commands are not discoverable ----------------------------------


@pytest.mark.parametrize("name", RETIRED_COMMANDS)
def test_a_retired_graph_command_is_not_discoverable(name):
    """Django discovers commands by module presence, so this is the real check."""
    assert name not in get_commands()


def test_the_surviving_commands_are_still_discoverable():
    commands = get_commands()

    assert commands["sync_oigusloome_public"] == "apps.legal_work"
    # The manual import never depended on Graph and remains useful for
    # acceptance testing a workbook from a local path.
    assert commands["import_oigusloome"] == "apps.legal_work"


# -- no Graph configuration remains required ----------------------------


@pytest.mark.parametrize("name", RETIRED_SETTINGS)
def test_no_retired_graph_setting_is_defined(settings, name):
    assert not hasattr(settings, name), f"{name} should have been retired with the route"


def test_the_public_route_configuration_is_untouched(settings):
    assert hasattr(settings, "OIGUSLOOME_PUBLIC_URL")
    assert hasattr(settings, "LEGAL_WORK_MAX_DOWNLOAD_BYTES")
    assert hasattr(settings, "LEGAL_WORK_SOURCE_SLUG")


def test_the_env_example_offers_no_graph_variable():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    for name in RETIRED_SETTINGS:
        assert name not in text
    # The one variable the surviving route needs is still documented, and still
    # blank so no sharing URL can enter Git.
    assert "OIGUSLOOME_PUBLIC_URL=" in text
    assert "OIGUSLOOME_PUBLIC_URL=\n" in text or text.rstrip().endswith("OIGUSLOOME_PUBLIC_URL=")


# -- the dependency is gone ---------------------------------------------


def test_msal_is_absent_from_the_declared_dependencies():
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = manifest["project"]["dependencies"]

    assert not any(name.lower().startswith("msal") for name in declared)


def test_msal_is_absent_from_the_resolved_lock():
    """The lock is what CI and the image actually install."""
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert 'name = "msal"' not in lock


def test_msal_cannot_be_imported():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("msal")


# -- exactly one recurring route ----------------------------------------


def test_only_one_recurring_workbook_collection_command_exists():
    """A second recurring route *to the workbook* is what created the drift.

    `import_oigusloome` is deliberately not counted: it reads a local path an
    operator supplies by hand and is not scheduled.

    The current-topic commands are counted separately below. They collect a
    different source and publish different models; the rule this test protects
    is that the **workbook** has exactly one scheduled route, not that the app
    owns exactly one command.
    """
    workbook_commands = {
        name
        for name, app in get_commands().items()
        if app == "apps.legal_work" and "oigusloome" in name
    }

    assert workbook_commands == {"sync_oigusloome_public", "import_oigusloome"}


def test_the_app_owns_exactly_the_commands_it_is_supposed_to():
    """Named in full, so a new scheduled route cannot appear unnoticed."""
    legal_work_commands = {name for name, app in get_commands().items() if app == "apps.legal_work"}

    assert legal_work_commands == {
        "sync_oigusloome_public",
        "import_oigusloome",
        # The public Koda.ee current-topic catalogue and its matcher.
        "sync_legal_current_topics",
        "match_legal_current_topics",
        # The `Hetkel käsil` archive, collected as a fallback source of
        # consultation links, and its separately calibrated matcher.
        "sync_legal_archived_topics",
        "match_legal_archived_topics",
        # The Chamber's own opinion documents: a private catalogue read from a
        # fixed directory, and a read-only integrity check over its blob store.
        # Neither is scheduled by this repository.
        "sync_legal_opinion_documents",
        "verify_legal_opinion_store",
    }
