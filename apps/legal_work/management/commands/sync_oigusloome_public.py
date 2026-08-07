"""Scheduled synchronisation of the legal-work workbook from a public link.

Safe to run from the host's scheduler every morning: overlapping runs are
refused by a PostgreSQL advisory lock, the checksum decides whether anything
changed, and any failure leaves the previously published snapshot exactly where
it was.

There is deliberately **no `--url` option**. The sharing URL is a bearer-style
secret and must come only from `OIGUSLOOME_PUBLIC_URL`, so it never enters shell
history, a process listing or a command log.

There is no `--force` either, and none is needed: the workbook is downloaded on
every run and the content checksum is authoritative.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another synchronisation was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.legal_work.models import SyncResult
from apps.legal_work.public_download import PublicUrlNotConfigured
from apps.legal_work.public_sync import synchronize_public_workbook
from apps.legal_work.sync import SyncLocked, advisory_lock


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Synchronize the legal-work workbook from the configured public "
        "OneDrive link and publish a snapshot."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Download and validate without publishing a snapshot."
        )
        parser.add_argument(
            "--allow-collapse",
            action="store_true",
            help=(
                "Publish even when the workbook holds far fewer records than the "
                "snapshot now on the dashboard. Use once, deliberately, when the "
                "dataset has genuinely shrunk."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            # The feed's own lock, so two overlapping invocations can never
            # both import even when the job is scheduled twice by mistake.
            with advisory_lock():
                outcome = synchronize_public_workbook(
                    dry_run=options["dry_run"],
                    allow_collapse=options["allow_collapse"],
                )
        except SyncLocked as error:
            self.exit_locked(error, as_json=as_json)
        except PublicUrlNotConfigured as error:
            # The operator's to fix, so it is reported plainly rather than as a
            # stack trace. The message names the variable, never its value.
            self.emit(
                as_json,
                {"result": "failed", "detail": str(error)},
                str(error),
                style=self.style.ERROR,
            )
            raise SystemExit(1) from None

        # Exactly one line, and only the keys `SyncOutcome` defines: result,
        # detail, snapshot id, reporting date, row count, dry-run flag and
        # warning-code counts. No URL, no host, no path, no header, no row.
        payload = outcome.as_dict()
        if outcome.result == SyncResult.FAILED:
            self.emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(outcome.exit_code)

        message = outcome.detail
        if outcome.result == SyncResult.IMPORTED and not outcome.dry_run:
            message = f"{outcome.detail} Kirjeid: {outcome.rows_imported}."
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
