"""Attach public Koda.ee page addresses to programme events.

Runs after `discover_koda_event_pages`, which is what supplies the candidates.
Cheap and purely local: no network access, no fetching, and the whole run is
arithmetic over rows already in the database. Safe to re-run at any time.

It changes no programme field. The event programme workbook remains the
authority on an event's name, date, type, delivery mode, tag, service code and
inclusion status; this only records which public page an event points at.

There is deliberately no way to name an event, a page or a URL. Matching is
all-or-nothing over the current programme snapshot, so no operator input can
steer a single decision.

Exit codes:

    0  matched, or a successful dry run
    1  failed
    3  another matching run was already going
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import EXIT_FAILED, FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.event_programme.event_match_sync import (
    LOCK_NAME,
    LOCKED_MESSAGE,
    EventMatchError,
    run_event_matching,
)


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Match current event-programme items against the discovered public "
        "Koda.ee event pages and publish the result."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser,
            dry_run_help="Decide everything and report the counts without publishing.",
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            with advisory_lock(LOCK_NAME, locked_message=LOCKED_MESSAGE):
                report = run_event_matching(dry_run=options["dry_run"])
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)
        except EventMatchError as error:
            payload = {"result": "failed", "detail": str(error)}
            self.emit(as_json, payload, f"Sobitamine ebaõnnestus: {error}", style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED) from error

        # Counts and a snapshot id. Never an event name, a page title or a URL.
        payload = report.as_dict()
        prefix = "Proovijooks: " if options["dry_run"] else ""
        message = (
            f"{prefix}Sündmusi vaadatud: {report.considered}. "
            f"Seotud: {report.matched}, ebaselgeid: {report.ambiguous}, "
            f"sidumata: {report.unmatched}. "
            f"Avalikke lehti: {report.resource_count}."
        )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
