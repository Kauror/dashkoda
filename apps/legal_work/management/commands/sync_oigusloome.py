"""Scheduled synchronisation of the legal-work workbook from OneDrive.

Safe to run from cron every day: overlapping runs are refused by a PostgreSQL
advisory lock, an unchanged remote file costs one metadata call, and any
failure leaves the previously published snapshot exactly where it was.

Exit codes:

    0  imported, or unchanged
    1  failed
    3  another synchronisation was already running
"""

import json

from django.core.management.base import BaseCommand

from apps.legal_work.graph import GraphNotConfigured
from apps.legal_work.models import SyncResult
from apps.legal_work.sync import EXIT_LOCKED, SyncLocked, advisory_lock, synchronize


class Command(BaseCommand):
    help = "Synchronize the legal-work workbook from OneDrive and publish a snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Download and validate without publishing a snapshot.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-download and re-import even when the remote file looks unchanged.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit one structured JSON line instead of prose.",
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            with advisory_lock():
                outcome = synchronize(dry_run=options["dry_run"], force=options["force"])
        except SyncLocked as error:
            self._emit(
                as_json,
                {"result": "locked", "detail": str(error)},
                f"Vahele jäetud: {error}",
                style=self.style.WARNING,
            )
            raise SystemExit(EXIT_LOCKED) from None
        except GraphNotConfigured as error:
            # Configuration problems are the operator's to fix, so they are
            # reported plainly rather than as a stack trace.
            self._emit(
                as_json,
                {"result": "failed", "detail": str(error)},
                str(error),
                style=self.style.ERROR,
            )
            raise SystemExit(1) from None

        payload = outcome.as_dict()
        if outcome.result == SyncResult.FAILED:
            self._emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(outcome.exit_code)

        message = outcome.detail
        if outcome.result == SyncResult.IMPORTED and not outcome.dry_run:
            message = f"{outcome.detail} Kirjeid: {outcome.rows_imported}."
        self._emit(as_json, payload, message, style=self.style.SUCCESS)

    def _emit(self, as_json: bool, payload: dict, message: str, *, style) -> None:
        if as_json:
            # One line, no secrets: only result, counts and a sanitized detail.
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(style(message))
