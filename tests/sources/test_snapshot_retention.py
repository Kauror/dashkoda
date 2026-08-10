"""The one command that deletes published rows.

Retention keeps current snapshots and roughly a week of retired history. What
makes it dangerous is not the policy but the schema: every match snapshot's
foreign key to a source snapshot is `CASCADE`, so deleting a retired source that
a **current** match still pins would take the live match with it, silently.

These tests are mostly about what must **not** happen.
"""

from __future__ import annotations

import datetime as dt
import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent
from apps.legal_work.models import (
    LegalCurrentTopicMatchSnapshot,
    LegalWorkSnapshot,
)
from apps.sources.retention import (
    DEFAULT_RETENTION_DAYS,
    FAMILIES,
    plan_retention,
    protected_ids,
    retention_cutoff,
)

pytestmark = pytest.mark.django_db


def run(*args) -> dict:
    output = StringIO()
    call_command("prune_snapshots", "--json", *args, stdout=output, stderr=StringIO())
    return json.loads(output.getvalue().strip())


def age(snapshot, *, days: int, field: str):
    """Backdate a snapshot past the window. `auto_now_add` needs an update()."""
    type(snapshot).objects.filter(pk=snapshot.pk).update(
        **{field: timezone.now() - dt.timedelta(days=days)}
    )
    snapshot.refresh_from_db()
    return snapshot


# The retention policy spans apps, and its sharpest cases involve legal-work
# snapshots. The fixtures live here rather than in a conftest because only this
# module needs them, and they go through the real publication paths so the rows
# are ones the application could actually have written.


@pytest.fixture
def legal_snapshot(db, tmp_path):
    """One published, current legal-work snapshot with its items."""
    from django.core.files import File

    from apps.legal_work.bootstrap import ensure_legal_work_source
    from apps.legal_work.importer import import_artifact
    from apps.sources.services import register_artifact
    from tests.legal_work.workbook_factory import write_workbook

    path = write_workbook(tmp_path / "synthetic.xlsx")
    with path.open("rb") as handle:
        artifact = register_artifact(
            source=ensure_legal_work_source(),
            upload=File(handle, name="dashkoda_oigusloome.xlsx"),
            original_name="dashkoda_oigusloome.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    return import_artifact(artifact, dry_run=False).snapshot


@pytest.fixture
def matched_current_world(legal_snapshot, monkeypatch):
    """A legal snapshot, a current-topic catalogue, and a current match of both.

    This is the shape the CASCADE hazard needs: a match snapshot that pins a
    source snapshot which could otherwise age out.
    """
    from apps.legal_work.bootstrap import ensure_current_topics_source
    from apps.legal_work.current_topic_match_sync import run_current_topic_matching
    from apps.legal_work.current_topic_sync import synchronize_current_topics
    from apps.legal_work.current_topics import collect_current_topics
    from tests.legal_work.current_topic_factory import (
        DETAIL_PREFIX,
        LISTING_PATH,
        FakeSite,
        card,
        detail,
        listing,
    )

    ensure_current_topics_source()
    site = FakeSite(
        {
            LISTING_PATH: listing(card("alpha", "Teema alpha")),
            f"{DETAIL_PREFIX}alpha": detail(title="Teema alpha"),
        }
    )
    monkeypatch.setattr("apps.legal_work.current_topics.fetch", site)
    synchronize_current_topics(collector=collect_current_topics)
    run_current_topic_matching()

    match = LegalCurrentTopicMatchSnapshot.objects.get(is_current=True)
    return legal_snapshot, match


class TestTheRegistry:
    def test_it_is_written_out_rather_than_discovered(self):
        """Introspection would enrol the next model before anyone decided.

        Parsed rather than scanned, for the same reason the command's test is:
        a comment explaining *why* the registry avoids `get_models()` must not
        be what fails the check.
        """
        import ast
        import inspect

        from apps.sources import retention

        tree = ast.parse(inspect.getsource(retention))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert "get_models" not in attributes
        assert "__subclasses__" not in attributes

    def test_every_family_resolves_and_declares_a_real_cutoff_field(self):
        from apps.sources.retention import family_model

        for family in FAMILIES:
            model = family_model(family)
            assert hasattr(model, family.cutoff_field), f"{family.model}"
            for pin in family.pins:
                model._meta.get_field(pin)

    def test_every_snapshot_model_in_the_codebase_is_registered(self):
        """A family nobody registered is never pruned — but should be noticed."""
        from django.apps import apps as django_apps

        from apps.sources.retention import NEVER_PRUNED

        registered = {family.model for family in FAMILIES}
        found = {
            f"{model._meta.app_label}.{model.__name__}"
            for model in django_apps.get_models()
            if model.__name__.endswith("Snapshot")
        }

        # A snapshot model is either prunable or deliberately permanent, and
        # both are decisions somebody has to write down. What must never happen
        # is a third state where nobody chose.
        accounted = registered | set(NEVER_PRUNED)
        assert found == accounted, f"unregistered snapshot models: {found - accounted}"
        assert not (registered & set(NEVER_PRUNED)), "a model cannot be both prunable and permanent"


class TestWhatIsAlwaysKept:
    def test_a_current_snapshot_older_than_the_window_is_kept(self, legal_snapshot):
        age(legal_snapshot, days=400, field="imported_at")
        assert legal_snapshot.is_current

        payload = run("--dry-run")

        assert payload["total_candidates"] == 0
        assert LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).exists()

    def test_a_retired_snapshot_inside_the_window_is_kept(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=DEFAULT_RETENTION_DAYS - 1, field="imported_at")

        assert run("--dry-run")["total_candidates"] == 0

    def test_a_retired_snapshot_beyond_the_window_is_a_candidate(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=DEFAULT_RETENTION_DAYS + 1, field="imported_at")

        assert run("--dry-run")["total_candidates"] == 1

    def test_the_boundary_is_evaluated_in_application_time(self):
        """Not the container's clock, like every other date in this project."""
        now = timezone.now()
        assert retention_cutoff(days=7, now=now) == now - dt.timedelta(days=7)
        assert timezone.is_aware(retention_cutoff())


class TestTheCascadeHazard:
    """The reason this policy exists rather than a bare date filter."""

    def test_an_old_snapshot_a_current_match_pins_is_protected(self, matched_current_world):
        legal, match = matched_current_world
        age(legal, days=400, field="imported_at")
        LegalWorkSnapshot.objects.filter(pk=legal.pk).update(is_current=False)

        payload = run("--dry-run")

        legal_report = next(
            f for f in payload["families"] if f["family"] == "legal_work.LegalWorkSnapshot"
        )
        assert legal_report["candidates"] == 0, "a current match's input was offered for deletion"
        assert legal_report["protected"] >= 1

    def test_a_live_prune_leaves_the_current_match_resolving(self, matched_current_world):
        legal, match = matched_current_world
        age(legal, days=400, field="imported_at")
        LegalWorkSnapshot.objects.filter(pk=legal.pk).update(is_current=False)

        run()

        assert LegalWorkSnapshot.objects.filter(pk=legal.pk).exists()
        assert LegalCurrentTopicMatchSnapshot.objects.filter(pk=match.pk, is_current=True).exists()
        assert match.matches.exists(), "the match's own rows cascaded away"

    def test_protection_is_transitive(self, matched_current_world):
        """A current match pins a source; whatever that pins is protected too."""
        legal, match = matched_current_world
        cutoff = retention_cutoff()
        protected = protected_ids(cutoff)

        assert match.pk in protected["legal_work.LegalCurrentTopicMatchSnapshot"]
        assert legal.pk in protected["legal_work.LegalWorkSnapshot"]
        assert match.current_topic_snapshot_id in protected["legal_work.CurrentTopicSnapshot"]

    def test_a_retired_match_does_not_protect_its_own_inputs(self, matched_current_world):
        """Both are old and neither is current, so the pair goes together.

        Protection follows from what is protected, never from what merely
        points — otherwise nothing old could ever be deleted.
        """
        legal, match = matched_current_world
        LegalCurrentTopicMatchSnapshot.objects.filter(pk=match.pk).update(is_current=False)
        LegalWorkSnapshot.objects.filter(pk=legal.pk).update(is_current=False)
        age(match, days=400, field="generated_at")
        age(legal, days=400, field="imported_at")

        payload = run("--dry-run")

        assert payload["total_candidates"] >= 2, "the old pair should be deletable together"

    def test_deleting_them_together_leaves_no_orphan(self, matched_current_world):
        legal, match = matched_current_world
        LegalCurrentTopicMatchSnapshot.objects.filter(pk=match.pk).update(is_current=False)
        LegalWorkSnapshot.objects.filter(pk=legal.pk).update(is_current=False)
        age(match, days=400, field="generated_at")
        age(legal, days=400, field="imported_at")

        run()

        assert not LegalWorkSnapshot.objects.filter(pk=legal.pk).exists()
        assert not LegalCurrentTopicMatchSnapshot.objects.filter(pk=match.pk).exists()


class TestTheDryRun:
    def test_it_deletes_nothing(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        payload = run("--dry-run")

        assert payload["total_candidates"] == 1
        assert payload["deleted_snapshots"] == 0
        assert LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).exists()

    def test_it_records_no_audit_event(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        run("--dry-run")

        assert not AuditEvent.objects.filter(action=AuditAction.SNAPSHOTS_PRUNED).exists()

    def test_it_reports_every_family_with_the_counts_an_operator_needs(self):
        payload = run("--dry-run")

        assert len(payload["families"]) == len(FAMILIES)
        for report in payload["families"]:
            assert set(report) >= {
                "family",
                "label",
                "current",
                "recent",
                "protected",
                "candidates",
                "estimated_child_rows",
                "oldest_candidate",
                "newest_candidate",
            }

    def test_its_candidates_are_the_ones_a_live_run_deletes(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        predicted = run("--dry-run")["total_candidates"]
        actual = run()["deleted_snapshots"]

        assert predicted == actual

    def test_it_estimates_the_child_rows_that_would_go_too(self, legal_snapshot):
        """A legal snapshot is one row and several hundred items."""
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        report = next(
            f for f in run("--dry-run")["families"] if f["family"] == "legal_work.LegalWorkSnapshot"
        )

        assert report["estimated_child_rows"] > 0


class TestTheLiveRun:
    def test_it_is_idempotent(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        first = run()
        second = run()

        assert first["deleted_snapshots"] == 1
        assert second["deleted_snapshots"] == 0
        assert second["total_candidates"] == 0

    def test_it_records_an_audit_event_per_family_it_touched(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        run()

        events = AuditEvent.objects.filter(action=AuditAction.SNAPSHOTS_PRUNED)
        assert events.count() == 1
        assert events.first().change_summary["snapshots_deleted"] == 1

    def test_it_never_deletes_a_current_snapshot(self, legal_snapshot):
        age(legal_snapshot, days=4000, field="imported_at")

        run()

        assert LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk, is_current=True).exists()

    def test_one_family_can_be_pruned_alone(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")

        payload = run("--source", "NewsSnapshot")

        assert len(payload["families"]) == 1
        assert LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).exists()

    def test_an_arbitrary_model_name_is_refused(self):
        from django.core.management.base import CommandError

        with pytest.raises((CommandError, SystemExit)):
            call_command(
                "prune_snapshots", "--source", "auth.User", stdout=StringIO(), stderr=StringIO()
            )

    def test_a_zero_day_window_is_refused(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="at least 1"):
            call_command("prune_snapshots", "--days", "0", stdout=StringIO(), stderr=StringIO())


class TestWhatIsNeverPruned:
    """Age is not a reason to delete anything that is not a snapshot."""

    def test_audit_events_survive(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")
        before = AuditEvent.objects.count()

        run()

        # The prune adds its own event; nothing existing is removed.
        assert AuditEvent.objects.count() >= before

    def test_feed_state_survives(self, legal_snapshot):
        from apps.legal_work.models import LegalWorkFeedState

        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")
        before = LegalWorkFeedState.objects.count()

        run()

        assert LegalWorkFeedState.objects.count() == before

    def test_source_artifacts_and_import_runs_survive(self, legal_snapshot):
        from apps.sources.models import ImportRun, SourceArtifact

        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=400, field="imported_at")
        artifacts, runs = SourceArtifact.objects.count(), ImportRun.objects.count()

        run()

        assert SourceArtifact.objects.count() == artifacts
        assert ImportRun.objects.count() == runs

    def test_the_command_reaches_no_model_outside_the_registry(self):
        """Nothing outside the registry can be reached from the command.

        Asserted against the parsed module, not its text. The command's own
        docstring *names* what it never prunes — audit events, artifacts,
        opinion blobs, `LegalMatter` identities — and a substring scan cannot
        tell a promise from a reference. It would fail on the documentation
        that makes the guarantee.
        """
        import ast
        import inspect

        from apps.sources.management.commands import prune_snapshots

        tree = ast.parse(inspect.getsource(prune_snapshots))

        # The only model module it may import is the audit one, and only for
        # the action enum. Every snapshot model arrives through `family_model`.
        model_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(".models")
        }
        assert model_modules == {"apps.audit.models"}, model_modules

        referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        referenced |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        referenced |= {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden = {"AuditEvent", "SourceArtifact", "OpinionDocumentBlob", "LegalMatter"}

        assert not referenced & forbidden, referenced & forbidden


class TestThePlanMatchesTheCommand:
    def test_plan_retention_covers_every_family(self):
        plans = plan_retention()

        assert [p.family.model for p in plans] == [f.model for f in FAMILIES]

    def test_a_longer_window_never_deletes_more(self, legal_snapshot):
        LegalWorkSnapshot.objects.filter(pk=legal_snapshot.pk).update(is_current=False)
        age(legal_snapshot, days=30, field="imported_at")

        week = run("--dry-run", "--days", "7")["total_candidates"]
        year = run("--dry-run", "--days", "365")["total_candidates"]

        assert year <= week
