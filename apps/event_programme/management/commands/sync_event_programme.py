"""Scheduled synchronisation of the event-programme workbook from a public link.

Safe to run from the host's scheduler every morning: overlapping runs are
refused by a PostgreSQL advisory lock, the checksum decides whether anything
changed, and any failure leaves the previously published snapshot exactly where
it was.

Schedule it *after* the Power Automate flow that produces the workbook. That
flow runs at 06:30 Europe/Tallinn and takes about a minute, so 07:00 leaves
ample room; the checksum makes an early run harmless anyway, since an unchanged
file simply reports "unchanged".

There is deliberately **no `--url` option**. The sharing URL is a bearer-style
secret and must come only from `EVENT_PROGRAMME_PUBLIC_URL`, so it never enters
shell history, a process listing or a command log.

There is no `--force` either, and none is needed: the workbook is downloaded on
every run and the content checksum is authoritative.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another synchronisation was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.event_programme.models import SyncResult
from apps.event_programme.public_download import PublicUrlNotConfigured
from apps.event_programme.sync import (
    SyncLocked,
    advisory_lock,
    synchronize_public_workbook,
)


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Synchronize the event-programme workbook from the configured public "
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
                "Publish even when the workbook holds far fewer events than the "
                "snapshot now on the dashboard. Use once, deliberately, when the "
                "programme has genuinely shrunk."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
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
        # detail, snapshot id, export timestamp, row count, dry-run flag and
        # warning-code counts. No URL, no host, no path, no header, no row.
        payload = outcome.as_dict()
        if outcome.result == SyncResult.FAILED:
            self.emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(outcome.exit_code)

        message = outcome.detail
        if outcome.result == SyncResult.IMPORTED and not outcome.dry_run:
            message = f"{outcome.detail} Sündmusi: {outcome.rows_imported}."
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
