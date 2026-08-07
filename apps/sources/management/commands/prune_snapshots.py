"""Delete retired snapshots older than the retention window.

Current snapshots matter. Roughly a week of retired history answers "what
changed yesterday". Older retired snapshots that nothing depends on are the only
thing this deletes.

**This is the one scheduled command that destroys data**, so it is deliberately
harder to misuse than the collectors:

- `--dry-run` reports exactly what a live run would delete and touches nothing;
- protection is re-evaluated **inside** each family's transaction, so a matcher
  that publishes between the plan and the delete cannot lose its inputs;
- each family commits separately, so one failure cannot leave another half done;
- `--source` accepts only a known family, never an arbitrary model name;
- a current snapshot can never be a candidate, and the live path asserts it
  again before deleting.

What it never touches: audit events, feed-state rows, source artifacts, source
files, opinion PDF blobs, `LegalMatter` identities, import runs. None of them is
a snapshot, and age alone is not a reason to delete anything.

Exit codes:

    0  pruned, or nothing to prune, or a successful dry run
    1  failed
    3  another prune was already running
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import record_event
from apps.core.feed_commands import EXIT_FAILED, FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.sources.retention import (
    DEFAULT_RETENTION_DAYS,
    FAMILIES,
    family_model,
    plan_family,
    protected_ids,
    retention_cutoff,
)

#: Its own lock, so a prune can neither block nor be blocked by a collector.
LOCK_NAME = "dashkoda.sources.prune_snapshots"

FAMILY_CHOICES = {family.model.split(".")[-1]: family for family in FAMILIES}


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Delete retired snapshots older than the retention window."

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser,
            dry_run_help=("Report exactly what a live run would delete, and delete nothing."),
        )
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=(
                f"Retention window in days (default {DEFAULT_RETENTION_DAYS}). "
                "Production policy is the default; a larger value is always safe."
            ),
        )
        parser.add_argument(
            "--source",
            choices=sorted(FAMILY_CHOICES),
            default=None,
            help=(
                "Prune only one snapshot family. A known family name, never a "
                "model path: nothing outside the registry can be named."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        dry_run = options["dry_run"]
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")

        families = (FAMILY_CHOICES[options["source"]],) if options["source"] else FAMILIES

        try:
            with advisory_lock(LOCK_NAME):
                payload = self._prune(families, days=days, dry_run=dry_run)
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        if payload["result"] == "failed":
            self.emit(as_json, payload, payload["detail"], style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        self.emit(as_json, payload, payload["detail"], style=self.style.SUCCESS)

    def _prune(self, families, *, days: int, dry_run: bool) -> dict:
        cutoff = retention_cutoff(days=days)
        protected = protected_ids(cutoff)

        reports = []
        deleted_snapshots = 0
        deleted_rows = 0
        failed = []

        for family in families:
            plan = plan_family(family, cutoff=cutoff, protected=protected[family.model])
            report = plan.as_dict()

            if not dry_run and plan.candidates:
                try:
                    snapshots, rows = self._delete(family, plan.candidates, days=days)
                    deleted_snapshots += snapshots
                    deleted_rows += rows
                    report["deleted"] = snapshots
                    report["deleted_rows"] = rows
                except Exception as error:  # noqa: BLE001 - one family must not stop the rest
                    failed.append(family.model)
                    report["error"] = f"{type(error).__name__}: {error}".replace("\n", " ")
            elif not dry_run:
                report["deleted"] = 0
                report["deleted_rows"] = 0

            reports.append(report)

        total_candidates = sum(r["candidates"] for r in reports)
        payload = {
            "result": "failed" if failed else ("dry_run" if dry_run else "pruned"),
            "dry_run": dry_run,
            "retention_days": days,
            "cutoff": cutoff.isoformat(),
            "total_candidates": total_candidates,
            "deleted_snapshots": deleted_snapshots,
            "deleted_rows": deleted_rows,
            "families": reports,
        }
        payload["detail"] = self._detail(payload, failed)
        return payload

    def _delete(self, family, candidates: list[int], *, days: int) -> tuple[int, int]:
        """Delete one family's candidates, re-checking protection inside the lock.

        The plan was made a moment ago. A matcher publishing in between would
        make one of these candidates a current match's input, and deleting it
        would cascade that match away. So the set is recomputed here, inside the
        transaction that does the deleting.
        """
        model = family_model(family)

        with transaction.atomic():
            fresh = protected_ids(retention_cutoff(days=days))[family.model]
            safe = (
                model.objects.filter(pk__in=candidates)
                .exclude(is_current=True)
                .exclude(pk__in=fresh)
            )
            ids = list(safe.values_list("pk", flat=True))
            if not ids:
                return 0, 0

            # Belt and braces: `delete()` on a queryset that somehow contained a
            # current snapshot would cascade a live match away.
            assert not model.objects.filter(pk__in=ids, is_current=True).exists()

            total, per_model = model.objects.filter(pk__in=ids).delete()
            snapshots = per_model.get(model._meta.label, 0)

            record_event(
                action=AuditAction.SNAPSHOTS_PRUNED,
                # The rows this describes are gone by the time anyone reads it,
                # so the family is named as text rather than pointing at a row.
                object_type=family.model,
                object_id=family.model.split(".")[-1],
                change_summary={
                    "family": family.model,
                    "retention_days": days,
                    "snapshots_deleted": snapshots,
                    "rows_deleted": total,
                },
            )
        return snapshots, total

    def _detail(self, payload: dict, failed: list[str]) -> str:
        if failed:
            return f"Kustutamine ebaõnnestus: {', '.join(failed)}."
        if payload["dry_run"]:
            return (
                f"Proovikäivitus: {payload['total_candidates']} hetkeseisu kustutataks. "
                "Midagi ei kustutatud."
            )
        if not payload["deleted_snapshots"]:
            return "Midagi ei olnud kustutada."
        return (
            f"Kustutatud {payload['deleted_snapshots']} hetkeseisu "
            f"({payload['deleted_rows']} rida kokku)."
        )
