"""Fetch the period user counts Koduleht cannot derive from its daily rows.

Separate from `sync_ga4` on purpose. That command reconciles completed
*reporting days* into immutable revisions and is deliberately three requests;
this one asks a different question — distinct people over a whole window — and
its answers are a cache rather than a published history. Folding the two
together would put a non-additive metric inside the machinery built for
additive ones.

Run it after `sync_ga4`, so the presets resolve against coverage that already
includes last night's day:

    python manage.py sync_ga4 && python manage.py sync_ga4_period_users

One request per reachable window: each period preset resolved against current
coverage, plus each preset's comparison window, de-duplicated. On a property
whose history is shorter than five years several presets clamp to the same
window and cost one request between them.

Custom ranges are not fetched — they are unbounded and each is asked once. The
card on Koduleht says a hand-picked window has no user count rather than
showing one belonging to a different period.

Exit codes:

    0  fetched, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import EXIT_FAILED, FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.visibility.ga4 import (
    Ga4ApiCollector,
    Ga4NotConfigured,
    Ga4ResponseError,
    get_configuration,
)
from apps.visibility.period_users import synchronize_period_users

LOCK_NAME = "dashkoda.visibility.sync_ga4_period_users"


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Ask Google Analytics for the distinct-user count of each period the "
        "Koduleht controls can select, and cache the answers."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser,
            dry_run_help="Query Google Analytics and report, without storing anything.",
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]

        try:
            with advisory_lock(LOCK_NAME):
                collector = Ga4ApiCollector(get_configuration())
                summary = synchronize_period_users(collector, dry_run=options["dry_run"])
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)
        except (Ga4NotConfigured, Ga4ResponseError) as error:
            payload = {"result": "failed", "detail": str(error)}
            self.emit(as_json, payload, str(error), style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED) from error

        payload = {
            "result": "imported" if summary.changed else "unchanged",
            "fetched": summary.fetched,
            "stored": summary.stored,
            "unchanged": summary.unchanged,
            "empty": summary.empty,
            "dry_run": options["dry_run"],
        }
        detail = (
            f"{summary.fetched} vahemikku päritud, {summary.stored} salvestatud, "
            f"{summary.unchanged} muutumatut, {summary.empty} ilma vastuseta."
        )
        self.emit(as_json, payload, detail, style=self.style.SUCCESS)
