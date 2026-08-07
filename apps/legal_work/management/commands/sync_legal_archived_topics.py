"""Scheduled collection of the Koda.ee `Hetkel käsil` archive.

The archive is the fallback source of consultation links: once a page leaves the
current listing it is found here, keeping the same canonical address.

Two shapes of run. `--full` walks every listing page and settles which entries
are still present; the ordinary run starts at the newest page and stops after a
couple of pages whose entries are all already known, which is what makes the
daily job cost two requests instead of 143.

Detail pages are read under a budget with **two priorities**. First, pages
shortlisted as candidates for a consultation-eligible legal record the current
matcher could not answer — those are read **regardless of age**, because
eligibility is about a record's status and says nothing about when its
consultation ran. Whatever budget is left then fills the recent background
window, newest first.

Repeated bounded runs accumulate — hydration from the previous snapshot is
carried forward — so the initial backfill is resumable rather than one enormous
crawl, and a run that could not finish its priority candidates does not report
`unchanged`.

There is deliberately **no `--url` option**. The archive address is fixed
configuration on an exact host allowlist.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, FeedResult, advisory_lock
from apps.legal_work.archived_topic_sync import LOCK_NAME, synchronize_archived_topics
from apps.legal_work.sync import EXIT_FAILED


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Collect the public Koda.ee 'Hetkel käsil' archive index, hydrate a "
        "bounded slice of its detail pages, and publish an archive snapshot."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Collect and validate without publishing a snapshot."
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help=(
                "Walk every archive listing page instead of stopping at the "
                "first already-known pages. Needed for the initial backfill and "
                "to re-settle which entries are still present."
            ),
        )
        parser.add_argument(
            "--max-detail-pages",
            type=int,
            default=None,
            metavar="N",
            help=(
                "How many detail pages this run may fetch. An integer only; "
                "there is no way to name a URL."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            with advisory_lock(LOCK_NAME):
                report = synchronize_archived_topics(
                    dry_run=options["dry_run"],
                    full=options["full"],
                    max_detail_pages=options["max_detail_pages"],
                )
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        # Counts, a snapshot id and progress flags. Never a title, a
        # summary, a URL or any page text.
        payload = report.as_dict()
        if report.result == FeedResult.FAILED:
            self.emit(as_json, payload, report.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        message = report.detail
        if report.result == FeedResult.IMPORTED:
            message = (
                f"{report.detail} Indeksis: {report.indexed_items}, "
                f"loetud: {report.detailed_items}, ootel: {report.pending_items}, "
                f"vigaseid: {report.failed_items}. "
                f"Prioriteetseid: {report.priority_candidate_count} "
                f"(loetud {report.priority_detailed_count}, "
                f"lugemata {report.priority_pending_count}). "
                f"Täielik: {'jah' if report.backfill_complete else 'ei'}."
            )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
