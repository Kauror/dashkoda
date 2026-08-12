"""Scheduled reading of the Smaily newsletter lists.

With no arguments this reads every segment once and publishes the reading if any
list has changed size. That is a single API request and it is idempotent: a day
whose numbers are unchanged produces nothing at all.

    python manage.py sync_smaily
    python manage.py sync_smaily --dry-run --json

Unlike `sync_ga4` there is **no backfill and no date range**. Smaily reports what
a list holds now and has no endpoint that answers what it held last March, so
newsletter history starts on the day collection started and grows forward. A
`--date` option would only let somebody file today's reading under yesterday.

There is deliberately **no `--subdomain`, `--username` or `--password` option**.
All three come from the environment, so none can enter shell history or a
process listing, and no argument can point the command at a different account.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import EXIT_FAILED, FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, FeedResult, advisory_lock
from apps.visibility.smaily_sync import LOCK_NAME, synchronize_smaily


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Read the Smaily newsletter lists and publish the reading if any list has changed size."

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Query and validate without publishing anything."
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]

        try:
            with advisory_lock(LOCK_NAME):
                outcome = synchronize_smaily(dry_run=options["dry_run"])
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        payload = self._payload(outcome)
        if outcome.result == FeedResult.FAILED:
            self.emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        self.emit(as_json, payload, outcome.detail, style=self.style.SUCCESS)

    def _payload(self, outcome) -> dict:
        """The whole JSON contract: counts and an outcome, nothing else.

        No subdomain, no API username, no password and no part of Smaily's
        response — and no segment list either. `withheld` carries metric keys
        and this repository's own sentences, which is what an operator needs to
        know a newsletter has no figure and why.

        Every key is named here rather than copied out of `outcome.extra`, so
        the JSON a scheduler parses cannot gain or lose fields depending on how
        the run ended.
        """
        return {
            "result": outcome.result,
            "detail": outcome.detail,
            "dry_run": outcome.dry_run,
            "observed_on": outcome.extra.get("observed_on"),
            "action": outcome.extra.get("action"),
            "segments_read": outcome.extra.get("segments_read", 0),
            "segment_rows_written": outcome.extra.get("segment_rows_written", 0),
            "newsletters_available": outcome.extra.get("newsletters_available", 0),
            "newsletters_withheld": outcome.extra.get("newsletters_withheld", 0),
            "withheld": outcome.extra.get("withheld", {}),
            "api_requests": outcome.extra.get("api_requests", 0),
            "api_retries": outcome.extra.get("api_retries", 0),
        }
