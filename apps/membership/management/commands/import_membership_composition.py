"""Import aggregate composition facts from a member-roster export.

    python manage.py import_membership_composition \\
        --roster <path> --snapshot-date 2026-06-09 --dry-run --json

The roster holds personal data. This command reads it, counts what the dashboard
needs and stores only the counts — no company name, registry code, address,
contact or comment is written to the database, printed here, or recorded in the
audit trail. The file itself is never stored either; the artifact registered
against the import carries its checksum and nothing else.

The snapshot date is required rather than read from the file name, because a
rename must not be able to change what the data means. Every tenure and every
joining cohort in the import is measured against it.

Re-running the identical file reports `unchanged` and writes nothing. Importing
a *different* file over an existing snapshot needs `--supersede-previous`, which
retires the old snapshot without deleting it.
"""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.membership.composition_import import (
    CompositionImportError,
    import_composition_snapshot,
)


class Command(BaseCommand):
    help = (
        "Import aggregate membership composition from a roster export. "
        "Stores counts only; no individual member is persisted or logged."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--roster",
            required=True,
            help="Path to the roster export (.xlsx). The file is never stored.",
        )
        parser.add_argument(
            "--snapshot-date",
            required=True,
            help=(
                "The date the roster describes, as YYYY-MM-DD. Required: the "
                "workbook states no date and a file name is not evidence."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read and validate everything, write no domain rows.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Print aggregate counts as JSON. Never prints source content.",
        )
        parser.add_argument(
            "--supersede-previous",
            action="store_true",
            help=(
                "Required to import over an existing snapshot. Marks the "
                "current one superseded. Nothing is deleted."
            ),
        )

    def handle(self, *args, **options):
        try:
            snapshot_date = date.fromisoformat(options["snapshot_date"])
        except ValueError as error:
            raise CommandError("--snapshot-date peab olema kujul YYYY-MM-DD.") from error

        # `timezone.localdate()`, not `date.today()`: the container clock runs
        # UTC while the application's day is Europe/Tallinn, and the wall clock
        # would reject today's snapshot as "future" for the first hours of
        # every Tallinn day.
        if snapshot_date > timezone.localdate():
            raise CommandError("--snapshot-date ei saa olla tulevikus.")

        try:
            result = import_composition_snapshot(
                options["roster"],
                snapshot_date=snapshot_date,
                dry_run=options["dry_run"],
                supersede_previous=options["supersede_previous"],
            )
        except CompositionImportError as error:
            raise CommandError(str(error)) from error

        if options["as_json"]:
            self.stdout.write(json.dumps(result.as_json(), ensure_ascii=False, sort_keys=True))
            return

        if result.unchanged:
            self.stdout.write(
                self.style.SUCCESS("Sama fail on juba imporditud. Muudatusi ei tehtud.")
            )
            return

        if result.dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Kuivkäivitus õnnestus. Ridu loetud: {result.rows_read}. "
                    "Andmeid ei salvestatud."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Imporditud koosseis seisuga {result.snapshot_date}: "
                    f"{result.rows_read} rida kokku, {result.values_written} koondnäitajat"
                    + (
                        f", asendatud varasemaid hetkeseise: {result.superseded}."
                        if result.superseded
                        else "."
                    )
                )
            )

        for diagnostic in result.diagnostics:
            self.stdout.write(self.style.WARNING(f"  hoiatus: {diagnostic}"))
