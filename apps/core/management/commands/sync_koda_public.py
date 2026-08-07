"""Scheduled collection of the three public Koda.ee sources.

Each source runs independently, under its own PostgreSQL advisory lock and its
own transaction. **Failure isolation is the point**: a broken events page must
not stop the member count being updated, and a news outage must not hold back
either of the others. One source failing is reported as a degraded run, not as a
total one.

Exit codes:

    0  every requested source imported or was unchanged
    1  every requested source failed
    2  at least one source failed while another succeeded (degraded)
    3  no requested source could take its lock

A partial failure deliberately returns non-zero, so a scheduled job shows up as
degraded rather than silently losing one feed, while the sources that did
succeed keep their freshly published data.
"""

import json

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, FeedResult, SourceOutcome, advisory_lock
from apps.events.sync import LOCK_NAME as EVENTS_LOCK
from apps.events.sync import synchronize_events
from apps.membership.sync import LOCK_NAME as MEMBERSHIP_LOCK
from apps.membership.sync import synchronize_membership
from apps.news.sync import LOCK_NAME as NEWS_LOCK
from apps.news.sync import synchronize_news

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2
EXIT_LOCKED = 3

RESULT_SUCCEEDED = "succeeded"
RESULT_PARTIAL = "partial_failure"
RESULT_FAILED = "failed"
RESULT_LOCKED = "locked"

# Ordered so the cheapest source runs first and a slow events crawl never
# delays the member count.
SOURCES = {
    "membership": (MEMBERSHIP_LOCK, synchronize_membership),
    "news": (NEWS_LOCK, synchronize_news),
    "events": (EVENTS_LOCK, synchronize_events),
}

LABELS = {
    "membership": "Liikmeskond",
    "news": "Uudised",
    "events": "Sündmused",
}


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Collect the public Koda.ee member count, news feed and events calendar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=["all", *SOURCES],
            default="all",
            help="Which source to synchronize. Defaults to all.",
        )
        self.add_output_arguments(
            parser, dry_run_help="Collect and validate without publishing anything."
        )

    def handle(self, *args, **options):
        requested = list(SOURCES) if options["source"] == "all" else [options["source"]]
        dry_run = options["dry_run"]

        results: dict[str, SourceOutcome] = {}
        locked: set[str] = set()

        for name in requested:
            lock_name, synchronize = SOURCES[name]
            try:
                # Per-source lock: one slow or stuck source cannot block the
                # others, and none of them collides with the legal-work job.
                with advisory_lock(lock_name):
                    results[name] = synchronize(dry_run=dry_run)
            except FeedLocked as error:
                locked.add(name)
                results[name] = SourceOutcome(result=RESULT_LOCKED, detail=str(error))

        overall, exit_code = self._overall(results, locked, requested)
        payload = {
            "result": overall,
            "dry_run": dry_run,
            "sources": {name: outcome.as_dict() for name, outcome in results.items()},
        }

        if options["as_json"]:
            # Exactly one line. Only results, sanitized details and aggregate
            # counts — never a member row, a registration code, a feed body or
            # an article.
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            for name in requested:
                outcome = results[name]
                self.stdout.write(self._style_for(outcome)(f"{LABELS[name]}: {outcome.detail}"))

        if exit_code:
            raise SystemExit(exit_code)

    def _overall(self, results, locked, requested) -> tuple[str, int]:
        succeeded = [name for name, outcome in results.items() if outcome.succeeded]
        failed = [name for name, outcome in results.items() if outcome.result == FeedResult.FAILED]

        if locked and len(locked) == len(requested):
            return RESULT_LOCKED, EXIT_LOCKED
        if not failed and not locked:
            return RESULT_SUCCEEDED, EXIT_OK
        if succeeded:
            return RESULT_PARTIAL, EXIT_PARTIAL
        return RESULT_FAILED, EXIT_FAILED

    def _style_for(self, outcome: SourceOutcome):
        if outcome.result == FeedResult.FAILED:
            return self.style.ERROR
        if outcome.result == RESULT_LOCKED:
            return self.style.WARNING
        return self.style.SUCCESS
