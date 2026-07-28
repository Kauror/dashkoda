"""Manual import of a canonical legal-work workbook from a local file.

For development and acceptance testing. It registers the file through the
ordinary source service and then runs the *same* importer the scheduled sync
uses, so a file that imports here imports identically in production.
"""

from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from apps.legal_work.bootstrap import ensure_legal_work_source
from apps.legal_work.importer import LegalWorkImportError, import_artifact
from apps.legal_work.sync import WORKBOOK_FILENAME
from apps.sources.models import SourceArtifact
from apps.sources.services import calculate_sha256, register_artifact


class Command(BaseCommand):
    help = "Import a canonical legal-work XLSX workbook from a local path."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Path to the canonical XLSX workbook.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate everything without publishing a snapshot.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"]).expanduser()
        if not path.is_file():
            raise CommandError(f"Faili ei leitud: {path}")

        source = ensure_legal_work_source()

        with path.open("rb") as handle:
            checksum, _size = calculate_sha256(handle)

        # Reuse rather than re-register: the same bytes under the same source
        # are one artifact, and the immutable-artifact rules forbid a duplicate.
        artifact = SourceArtifact.objects.filter(source=source, sha256=checksum).first()
        if artifact is None:
            with path.open("rb") as handle:
                artifact = register_artifact(
                    source=source,
                    upload=File(handle, name=WORKBOOK_FILENAME),
                    original_name=WORKBOOK_FILENAME,
                    mime_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                )

        try:
            result = import_artifact(artifact, dry_run=options["dry_run"])
        except LegalWorkImportError as error:
            raise CommandError(str(error)) from error

        if result.dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Kuivkäivitus õnnestus. Kirjeid: {result.import_run.rows_skipped}. "
                    "Hetkeseisu ei loodud."
                )
            )
            return

        snapshot = result.snapshot
        self.stdout.write(
            self.style.SUCCESS(
                f"Imporditud {result.rows_added} kirjet. "
                f"Seis {snapshot.reporting_date:%d.%m.%Y}, "
                f"töös {snapshot.open_record_count}, "
                f"hoiatustega {snapshot.warning_record_count}."
            )
        )
