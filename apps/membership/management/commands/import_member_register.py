"""Import the member roster's rows from a CRM export.

    python manage.py import_member_register \\
        --roster <path> --snapshot-date 2026-08-15 --dry-run --json

The export is read, a curated subset of its columns is stored, and the file
itself is never kept — the artifact registered against the import carries its
checksum and nothing else. Which columns are stored, and which are deliberately
not modelled at all, is written down in `apps/membership/models/register.py`.

The snapshot date is required rather than read from the file name: the export
states no date of its own, and a rename must not be able to change what the
data means. The page shows this date beside every figure the register produces,
because the list is accurate as of the export and drifts afterwards.

Re-running the identical file reports `unchanged` and writes nothing. A newer
export needs `--supersede-previous`, which retires the current snapshot without
deleting it or its rows.
"""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.membership.register_import import RegisterImportError, import_member_register


class Command(BaseCommand):
    help = (
        "Import the member roster's rows from a CRM export (UTF-16 TSV or CSV). "
        "Stores a curated subset of columns; no address, phone, e-mail, "
        "director name or comment is persisted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--roster",
            required=True,
            help="Path to the CRM export. The file itself is never stored.",
        )
        parser.add_argument(
            "--snapshot-date",
            required=True,
            help=(
                "The date the export describes, as YYYY-MM-DD. Required: the "
                "file states no date and a file name is not evidence."
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
        # UTC while the application's day is Europe/Tallinn.
        if snapshot_date > timezone.localdate():
            raise CommandError("--snapshot-date ei saa olla tulevikus.")

        try:
            result = import_member_register(
                options["roster"],
                snapshot_date=snapshot_date,
                dry_run=options["dry_run"],
                supersede_previous=options["supersede_previous"],
            )
        except RegisterImportError as error:
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
                    f"Imporditud liikmete nimekiri seisuga {result.snapshot_date}: "
                    f"{result.rows_read} rida loetud, {result.rows_written} kirjet salvestatud"
                    + (
                        f", asendatud varasemaid hetkeseise: {result.superseded}."
                        if result.superseded
                        else "."
                    )
                )
            )

        for diagnostic in result.diagnostics:
            self.stdout.write(self.style.WARNING(f"  hoiatus: {diagnostic}"))
