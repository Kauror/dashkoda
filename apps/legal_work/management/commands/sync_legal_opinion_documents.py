"""Build the private catalogue of the Chamber's opinion documents.

Reads the fixed source inbox, validates and stores each document under its own
digest, extracts its text, and publishes a catalogue snapshot once — and only
once — every document has reached a terminal state.

Bounded and resumable by design. `--max-documents` caps how much one run does;
running the command again continues, and nothing already validated, stored or
extracted is paid for twice. That is what makes the initial backfill a series
of short runs rather than one long job that fails whole.

There is deliberately **no path or URL option**. Both roots are configuration,
so no operator input can steer a read or a write, and no filename can enter a
process listing or shell history.

Exit codes:

    0  imported, unchanged, partially processed, or a successful dry run
    1  failed
    3  another build was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.opinion_catalogue_sync import (
    LOCK_NAME,
    RESULT_FAILED,
    RESULT_IMPORTED,
    RESULT_PARTIAL,
    synchronize_opinion_documents,
)
from apps.legal_work.sync import EXIT_FAILED


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Read the private opinion-document inbox, validate and store each "
        "document, extract its text, and publish a complete catalogue snapshot."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Scan and validate without publishing or writing a managed blob."
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help=(
                "Rebuild the catalogue even when the source manifest is "
                "unchanged. Needed after an extractor change."
            ),
        )
        parser.add_argument(
            "--max-documents",
            type=int,
            default=None,
            metavar="N",
            help=(
                "How many documents this run may process. An integer only; "
                "there is no way to name a file or a path."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            with advisory_lock(LOCK_NAME):
                report = synchronize_opinion_documents(
                    dry_run=options["dry_run"],
                    full=options["full"],
                    max_documents=options["max_documents"],
                )
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        # Counts, a snapshot id, a checksum prefix and the extractor
        # version. Never a filename, a recipient, a subject, document text,
        # a storage path or a full digest.
        payload = report.as_dict()
        if report.result == RESULT_FAILED:
            self.emit(as_json, payload, report.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        message = report.detail
        if report.result in (RESULT_IMPORTED, RESULT_PARTIAL):
            message = (
                f"{report.detail} Korras: {report.valid_entries}, "
                f"karantiinis: {report.quarantined_entries}, "
                f"loetud: {report.extracted_entries}, "
                f"OCR-i vajab: {report.needs_ocr_entries}, "
                f"lugemata: {report.failed_entries}."
            )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
