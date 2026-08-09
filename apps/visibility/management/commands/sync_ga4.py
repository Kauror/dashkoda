"""Scheduled reconciliation of recent Google Analytics reporting days.

**What the schedule runs is the reconciliation, never the backfill.** With no
arguments this re-reads the last eight completed days and republishes any whose
figures GA4 has revised. That is three API requests and it is idempotent: a day
whose normalised figures are unchanged produces nothing at all.

Historical import is the same command with a range, run by hand once:

    python manage.py sync_ga4 --start-date 2023-06-01 --end-date 2023-12-31

Re-running a range that is already imported is safe and cheap in the database —
it re-reads from GA4 and publishes nothing — so a backfill interrupted halfway
is resumed by running it again. Nothing is stored to make that work; it works
because publication is decided by a checksum.

There is deliberately **no `--property` and no `--credentials` option**. Both
come from the environment, so neither can enter shell history or a process
listing, and no argument can point the command at a different property.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, FeedResult, advisory_lock
from apps.legal_work.sync import EXIT_FAILED
from apps.visibility.ga4_sync import (
    BACKFILL_CHUNK_DAYS,
    LOCK_NAME,
    RECONCILIATION_DAYS,
    reconciliation_window,
    synchronize_ga4,
)


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Re-read the last completed Google Analytics reporting days and publish "
        "any whose figures have changed. With a range, import history."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Query and validate without publishing anything."
        )
        parser.add_argument(
            "--date",
            dest="single_date",
            default=None,
            help=(
                "One reporting day as YYYY-MM-DD. Shorthand for the same value "
                "in --start-date and --end-date."
            ),
        )
        parser.add_argument(
            "--start-date",
            default=None,
            help="First reporting day of a range, as YYYY-MM-DD.",
        )
        parser.add_argument(
            "--end-date",
            default=None,
            help=(
                "Last reporting day of a range, as YYYY-MM-DD. Defaults to "
                "yesterday, and is clamped to it: today has not finished."
            ),
        )
        parser.add_argument(
            "--days",
            type=int,
            default=RECONCILIATION_DAYS,
            help=(
                f"How many completed days to reconcile when no range is given "
                f"(default {RECONCILIATION_DAYS})."
            ),
        )
        parser.add_argument(
            "--chunk-days",
            type=int,
            default=BACKFILL_CHUNK_DAYS,
            help=(
                f"How many days to ask GA4 for at a time (default "
                f"{BACKFILL_CHUNK_DAYS}). Lower it only if a range fails on size."
            ),
        )
        parser.add_argument(
            "--no-pages",
            action="store_true",
            help=(
                "Collect site totals without page rows. A day that already has "
                "page detail is then left alone rather than replaced by a "
                "narrower reading."
            ),
        )
        parser.add_argument(
            "--no-channels",
            action="store_true",
            help="Collect without acquisition-channel rows.",
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]

        # Validated before the window is resolved, not after: `--days 0` reaches
        # `reconciliation_window` first and raises `ValueError`, which an
        # operator sees as a traceback rather than as the sentence naming what
        # they got wrong.
        if options["chunk_days"] < 1:
            raise CommandError("--chunk-days peab olema vähemalt 1.")
        if options["days"] < 1:
            raise CommandError("--days peab olema vähemalt 1.")

        start, end = self._window(options)

        try:
            with advisory_lock(LOCK_NAME):
                outcome = synchronize_ga4(
                    dry_run=options["dry_run"],
                    start=start,
                    end=end,
                    with_pages=not options["no_pages"],
                    with_channels=not options["no_channels"],
                    chunk_days=options["chunk_days"],
                )
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        payload = self._payload(outcome)
        if outcome.result == FeedResult.FAILED:
            self.emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        self.emit(as_json, payload, outcome.detail, style=self.style.SUCCESS)

    def _window(self, options) -> tuple[date | None, date | None]:
        """The range asked for, or `(None, None)` for the ordinary window."""
        single = self._date(options["single_date"], "--date")
        start = self._date(options["start_date"], "--start-date")
        end = self._date(options["end_date"], "--end-date")

        if single is not None:
            if start is not None or end is not None:
                raise CommandError("--date ja --start-date/--end-date on teineteist välistavad.")
            return single, single

        if start is None and end is None:
            if options["days"] != RECONCILIATION_DAYS:
                return reconciliation_window(days=options["days"])
            return None, None

        if start is not None and end is not None and end < start:
            raise CommandError("--end-date ei saa olla enne --start-date väärtust.")
        return start, end

    def _date(self, raw: str | None, flag: str) -> date | None:
        if raw is None:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as error:
            raise CommandError(f"{flag} peab olema kujul YYYY-MM-DD, saadi: {raw}") from error

    def _payload(self, outcome) -> dict:
        """The whole JSON contract: counts and a window, nothing else.

        No property ID, no credential path, no token and no part of Google's
        response — and no page list either. A scheduler log is not where a
        thousand URLs belong, and the counts are what an operator reads to know
        whether the run did anything.
        """
        payload = {
            "result": outcome.result,
            "detail": outcome.detail,
            "dry_run": outcome.dry_run,
        }
        payload.update(outcome.extra)
        return payload
