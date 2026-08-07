"""The output mechanics nine feed commands used to write out by hand.

This is a behaviour-preserving refactor, so what these tests pin is that
**nothing** an operator or a scheduler sees has moved: the same two flags, the
same one-line output, the same locked payload, the same exit codes.

They also pin the boundary. The mixin owns emission and the locked result; it
owns no payload schema, so each command's JSON keys stay its own. A shared
payload is exactly how one feed's log would quietly start carrying a field
somebody else's feed publishes.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command, load_command_class

from apps.core.feed_commands import (
    EXIT_FAILED,
    EXIT_LOCKED,
    EXIT_OK,
    FeedCommandOutputMixin,
)

#: Every command that runs one scheduled feed, as `(app_label, name)`.
FEED_COMMANDS = [
    ("core", "sync_koda_public"),
    ("event_programme", "sync_event_programme"),
    ("legal_work", "match_legal_archived_topics"),
    ("legal_work", "match_legal_current_topics"),
    ("legal_work", "match_legal_opinion_documents"),
    ("legal_work", "sync_legal_archived_topics"),
    ("legal_work", "sync_legal_current_topics"),
    ("legal_work", "sync_legal_opinion_documents"),
    ("legal_work", "sync_oigusloome_public"),
    ("visibility", "sync_ga4"),
]


def command(app_label: str, name: str):
    return load_command_class(f"apps.{app_label}", name)


class TestTheExitCodes:
    def test_they_are_the_documented_three(self):
        assert (EXIT_OK, EXIT_FAILED, EXIT_LOCKED) == (0, 1, 3)

    def test_legal_work_still_re_exports_them(self):
        """Callers and tests have always imported them from there."""
        from apps.legal_work import sync

        assert (sync.EXIT_OK, sync.EXIT_FAILED, sync.EXIT_LOCKED) == (0, 1, 3)


@pytest.mark.parametrize(("app_label", "name"), FEED_COMMANDS, ids=[n for _, n in FEED_COMMANDS])
class TestEveryFeedCommand:
    def test_it_uses_the_shared_output_mechanics(self, app_label, name):
        assert isinstance(command(app_label, name), FeedCommandOutputMixin)

    def test_it_still_takes_both_output_flags(self, app_label, name):
        parser = command(app_label, name).create_parser("manage.py", name)
        options = {action.dest for action in parser._actions}

        assert {"dry_run", "as_json"} <= options

    def test_it_no_longer_carries_its_own_emitter(self, app_label, name):
        assert not hasattr(command(app_label, name), "_emit")

    def test_its_dry_run_help_is_its_own(self, app_label, name):
        """A generic sentence would be documentation true of nothing."""
        parser = command(app_label, name).create_parser("manage.py", name)
        help_text = next(a.help for a in parser._actions if a.dest == "dry_run")

        assert help_text and help_text.strip()


class TestTheLockedResult:
    """One shape for every feed, because a scheduler reads all of them."""

    def _locked_run(
        self, monkeypatch, module_path: str, command_name: str, lock_attr="advisory_lock"
    ):
        import importlib
        from contextlib import contextmanager

        from apps.core.feeds import FeedLocked

        module = importlib.import_module(module_path)

        @contextmanager
        def held(*args, **kwargs):
            raise FeedLocked("Teine sünkroonimine juba käib.")
            yield  # pragma: no cover

        monkeypatch.setattr(module, lock_attr, held)
        output = StringIO()
        with pytest.raises(SystemExit) as exit_info:
            call_command(command_name, "--json", stdout=output, stderr=StringIO())
        return exit_info.value.code, output.getvalue()

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("module_path", "command_name"),
        [
            (
                "apps.legal_work.management.commands.sync_legal_current_topics",
                "sync_legal_current_topics",
            ),
            (
                "apps.legal_work.management.commands.match_legal_current_topics",
                "match_legal_current_topics",
            ),
            ("apps.visibility.management.commands.sync_ga4", "sync_ga4"),
            (
                "apps.legal_work.management.commands.sync_oigusloome_public",
                "sync_oigusloome_public",
            ),
        ],
    )
    def test_it_exits_three_and_reports_locked(self, monkeypatch, module_path, command_name):
        code, output = self._locked_run(monkeypatch, module_path, command_name)

        assert code == EXIT_LOCKED
        payload = json.loads(output.strip())
        assert payload == {"result": "locked", "detail": "Teine sünkroonimine juba käib."}

    @pytest.mark.django_db
    def test_the_prose_form_names_the_reason(self, monkeypatch):
        import importlib
        from contextlib import contextmanager

        from apps.core.feeds import FeedLocked

        module = importlib.import_module(
            "apps.legal_work.management.commands.sync_legal_current_topics"
        )

        @contextmanager
        def held(*args, **kwargs):
            raise FeedLocked("Allika x sünkroonimine juba käib.")
            yield  # pragma: no cover

        monkeypatch.setattr(module, "advisory_lock", held)
        output = StringIO()
        with pytest.raises(SystemExit):
            call_command("sync_legal_current_topics", stdout=output, stderr=StringIO())

        assert output.getvalue().strip() == "Vahele jäetud: Allika x sünkroonimine juba käib."


class TestTheMixinOwnsNoPayload:
    """The boundary the brief draws, asserted rather than trusted."""

    def test_it_defines_no_payload_method(self):
        assert not hasattr(FeedCommandOutputMixin, "_payload")
        assert not hasattr(FeedCommandOutputMixin, "payload")

    def test_it_imports_nothing_from_the_application(self):
        """The strongest form of the boundary, and the easiest to keep.

        Output mechanics need the standard library and nothing else. A module
        that cannot import a model, a service or a lock cannot grow feed logic
        by accident.
        """
        import ast
        import inspect

        from apps.core import feed_commands

        tree = ast.parse(inspect.getsource(feed_commands))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert imported == {"__future__", "json", "typing"}, imported

    def test_it_defines_only_output_methods(self):
        public = {name for name in vars(FeedCommandOutputMixin) if not name.startswith("__")}

        assert public == {"add_output_arguments", "emit", "exit_locked"}

    def test_each_command_keeps_its_own_json_keys(self):
        """Two feeds' payloads must not have collapsed into one schema."""
        from apps.legal_work.management.commands import sync_legal_current_topics
        from apps.visibility.management.commands import sync_ga4

        topics = sync_legal_current_topics.Command()._payload(
            type("O", (), {"result": "imported", "detail": "", "dry_run": False, "extra": {}})()
        )
        ga4 = sync_ga4.Command()._payload(
            type("O", (), {"result": "imported", "detail": "", "dry_run": False, "extra": {}})()
        )

        assert set(topics) != set(ga4)
        assert "item_count" in topics
        assert "figures_reported" in ga4
