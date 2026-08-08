"""Collect the public Koda.ee opinion corpus and publish it as a snapshot.

Two modes. `--full` performs the historical walk across the configured window
(2025 onwards) and must succeed once before incremental runs are allowed; the
default incremental run reads only the listing edge plus a short refresh
overlap, and carries everything else forward from the previous snapshot.

Rerunning either mode is cheap and idempotent: a known attachment URL whose
bytes are already stored is never downloaded again, identical corpus content
publishes nothing, and a failed run leaves the previous snapshot current.

There is deliberately **no URL option**. Every address this command touches is
configuration on a fixed host allowlist.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.public_opinion_sync import (
    LOCK_NAME,
    RESULT_FAILED,
    synchronize_public_opinions,
)
from apps.legal_work.sync import EXIT_FAILED


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Walk the Koda.ee Meie arvamus and news listings, read opinion "
        "articles and their attached PDFs, and publish the accumulated "
        "public opinion corpus as an immutable snapshot."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser,
            dry_run_help=(
                "Crawl and validate without publishing a snapshot or writing a managed blob."
            ),
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help=(
                "Walk the whole configured historical window instead of the "
                "listing edge. Required once before incremental runs."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            with advisory_lock(LOCK_NAME):
                report = synchronize_public_opinions(
                    dry_run=options["dry_run"],
                    full=options["full"],
                )
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        # Counts, a snapshot id and a checksum prefix. Never a URL, a title,
        # a filename, document text or a full digest.
        payload = report.as_dict()
        if report.result == RESULT_FAILED:
            self.emit(as_json, payload, report.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        message = (
            f"{report.detail} Uusi lehti: {report.new_pages}, uusi faile: "
            f"{report.new_blobs}, vigaseid: {report.invalid_documents}."
        )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
