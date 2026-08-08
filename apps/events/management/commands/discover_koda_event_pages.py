"""Discover public Koda.ee event pages and keep them.

A different job from `sync_koda_public --source events`, which publishes the
*upcoming* calendar and drops an event once it has finished. This one builds the
durable catalogue of event **pages**, including finished ones, so the event
programme's 2018–2024 rows can be given a public address.

Two shapes of run. `--full` re-reads every page the sitemap names, which is what
the initial backfill and any later re-settling needs. The ordinary run reads
only pages it has never seen plus those outside the recheck window, which on a
settled catalogue is a handful of requests.

Runs accumulate. Resources are never removed and the per-run detail budget is a
cap rather than a position, so a backfill that stops at the cap is continued
simply by running again — there is no cursor to lose and no partial state to
clean up. A run that hit the cap or failed a fetch says so: `is_complete` is
false and the snapshot carries a warning code.

There is deliberately **no `--url` option**. The sitemap address is fixed
configuration on an exact host allowlist, and the only pages read are the ones
it names under the event path prefix.

This command writes no event-programme field. It records addresses; the
programme workbook remains the authority on what an event is.

Exit codes:

    0  discovered, or a successful dry run
    1  failed
    3  another discovery run was already going
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import EXIT_FAILED, FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.events.collector import EventCollectionError
from apps.events.public_models import DiscoveryMode
from apps.events.public_sync import LOCK_NAME, LOCKED_MESSAGE, discover_event_pages


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Walk the Koda.ee sitemap and record every public event page it names, "
        "keeping pages for events that have already happened."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser,
            dry_run_help="Crawl and report what would change without writing anything.",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help=(
                "Re-read every event page the sitemap names instead of only the "
                "unknown and stale ones. Needed for the initial backfill."
            ),
        )
        parser.add_argument(
            "--max-detail-pages",
            type=int,
            default=None,
            metavar="N",
            help=(
                "How many event pages this run may fetch. An integer only; there "
                "is no way to name a URL."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        mode = DiscoveryMode.FULL if options["full"] else DiscoveryMode.INCREMENTAL

        try:
            with advisory_lock(LOCK_NAME, locked_message=LOCKED_MESSAGE):
                tally = discover_event_pages(
                    mode=mode,
                    max_detail_pages=options["max_detail_pages"],
                    dry_run=options["dry_run"],
                )
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)
        except EventCollectionError as error:
            # The sitemap could not be read, or said something implausible. No
            # snapshot is written, so the catalogue is exactly as it was.
            payload = {"result": "failed", "detail": str(error)}
            self.emit(as_json, payload, f"Avastamine ebaõnnestus: {error}", style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED) from error

        # Counts and flags. Never a title, a URL or any page text.
        payload = tally.as_dict()
        prefix = "Proovijooks: " if options["dry_run"] else ""
        message = (
            f"{prefix}Sündmuste lehti nähtud: {tally.urls_seen}, "
            f"laaditud: {tally.pages_fetched}. "
            f"Uusi: {tally.created}, muutunud: {tally.updated}, "
            f"muutumata: {tally.unchanged}, vigu: {tally.errors}. "
            f"Täielik: {'jah' if tally.is_complete else 'ei'}."
        )
        style = self.style.SUCCESS if tally.is_complete else self.style.WARNING
        self.emit(as_json, payload, message, style=style)
