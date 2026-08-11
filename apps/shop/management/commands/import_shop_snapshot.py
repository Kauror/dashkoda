"""Import the manual E-pood commerce package.

    python manage.py import_shop_snapshot --package <path> --validate-only --json
    python manage.py import_shop_snapshot --package <path> --json

The command accepts a package, never a directory of loose CSV files: the
manifest and its per-file checksums are what make the export approved, and a
directory has neither. Repeating an identical live import reports `unchanged`
and writes nothing.

There is deliberately no upload route, no webhook and no URL option. The package
is handed to this command from a private path by a person, and the path never
enters the database, the interface or an audit summary.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.shop.importing import ShopImportError, import_shop_package


class Command(BaseCommand):
    help = "Import the manual Koda.ee E-pood commerce package (ZIP with manifest)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--package",
            required=True,
            help="Path to the approved E-pood package ZIP.",
        )
        parser.add_argument(
            "--validate-only",
            action="store_true",
            dest="validate_only",
            help="Validate the whole contract and write no domain rows.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Print aggregate counts as JSON. Never prints source content.",
        )

    def handle(self, *args, **options):
        try:
            result = import_shop_package(
                options["package"],
                dry_run=options["validate_only"],
            )
        except ShopImportError as error:
            raise CommandError(str(error)) from error

        if options["as_json"]:
            self.stdout.write(json.dumps(result.as_json(), ensure_ascii=False, sort_keys=True))
            return

        if result.unchanged:
            self.stdout.write(
                self.style.SUCCESS("E-poe andmed on juba imporditud. Muudatusi ei tehtud.")
            )
            return

        if result.dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "Kontroll õnnestus. Kirjeid kontrollitud: "
                    f"{sum(result.counts.values())}. Andmeid ei salvestatud."
                )
            )
            return

        counts = result.counts
        self.stdout.write(
            self.style.SUCCESS(
                f"Imporditud seisuga {result.source_as_of:%d.%m.%Y} "
                f"({result.coverage_start:%d.%m.%Y}–{result.coverage_end:%d.%m.%Y}): "
                f"{counts.get('products', 0)} toote vaatlust, "
                f"{counts.get('product_paths', 0)} lehte, "
                f"{counts.get('daily_facts', 0)} päevafakti."
            )
        )
