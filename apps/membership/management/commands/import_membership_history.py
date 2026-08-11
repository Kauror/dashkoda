"""One-time import of the approved historical membership package.

Run once per environment. Every later board report is entered by a staff user
through the admin form; there is no schedule and nothing to install.

    python manage.py import_membership_history --package <path> --dry-run --json
    python manage.py import_membership_history --package <path> --json

The command accepts a package, never a directory of loose CSV files: the
manifest and its checksums are what make the data approved, and a directory has
neither. Repeating the identical live import reports `unchanged` and writes
nothing.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.membership.history_import import (
    MembershipHistoryImportError,
    import_history_package,
)


class Command(BaseCommand):
    help = "Import the approved historical membership package (ZIP with manifest)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--package",
            required=True,
            help="Path to the approved import package ZIP.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate everything and write no domain rows.",
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
                "Required when a different package is imported into a history "
                "that already holds one. Marks the existing observations "
                "superseded and no longer preferred. Nothing is deleted."
            ),
        )

    def handle(self, *args, **options):
        try:
            result = import_history_package(
                options["package"],
                dry_run=options["dry_run"],
                supersede_previous=options["supersede_previous"],
            )
        except MembershipHistoryImportError as error:
            raise CommandError(str(error)) from error

        if options["as_json"]:
            self.stdout.write(json.dumps(result.as_json(), ensure_ascii=False, sort_keys=True))
            return

        if result.unchanged:
            self.stdout.write(self.style.SUCCESS("Pakett on juba imporditud. Muudatusi ei tehtud."))
            return

        if result.dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "Kuivkäivitus õnnestus. Kirjeid kontrollitud: "
                    f"{sum(result.counts.values())}. Andmeid ei salvestatud."
                )
            )
            return

        counts = result.counts
        self.stdout.write(
            self.style.SUCCESS(
                f"Imporditud {result.rows_added} rida: "
                f"{counts.get('source_documents', 0)} dokumenti, "
                f"{counts.get('observations', 0)} vaatlust, "
                f"{counts.get('monthly_values', 0)} kuu väärtust, "
                f"{counts.get('size_movements', 0)} liikumist, "
                f"{counts.get('removal_reasons', 0)} lahkumise põhjust, "
                f"{counts.get('issues', 0)} hoiatust, "
                f"{counts.get('conflicts', 0)} vastuolu."
            )
        )
