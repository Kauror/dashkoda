"""Catalogue completed Smaily campaigns and reconcile their statistics.

With no arguments this lists recent completed campaigns, writes down any it has
not seen before, and re-reads the aggregate statistics of every campaign
completed in the last fortnight — because opens and clicks accrue for days after
a send and then stop.

    python manage.py sync_smaily_campaigns
    python manage.py sync_smaily_campaigns --dry-run --json

The historical backfill is the same command with a larger cap, run by hand once:

    python manage.py sync_smaily_campaigns --stats-limit 250

Re-running it is safe and cheap: a campaign whose figures are unchanged
publishes nothing, so a backfill interrupted halfway is resumed by running it
again. Nothing is stored to make that work — it works because publication is
decided by a checksum.

This is a **separate command from `sync_smaily`** rather than a flag on it. The
two take different locks and fail independently: a campaign read that fails must
not make the newsletter subscriber figures look stale when they were collected
successfully minutes earlier.

There is deliberately **no `--detailed` option and no way to reach one**. Smaily
returns per-recipient rows for `detailed=1` — who opened, who clicked, from
which address — and this application has no field for any of it.

Exit codes:

    0  catalogued, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

from django.core.management.base import BaseCommand, CommandError

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, FeedResult, advisory_lock
from apps.legal_work.sync import EXIT_FAILED
from apps.visibility.smaily_campaign_sync import (
    CAMPAIGN_LIST_LIMIT,
    LOCK_NAME,
    MAX_STATS_PER_RUN,
    synchronize_campaigns,
)


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Catalogue completed Smaily campaigns and publish the aggregate "
        "statistics of the recent ones."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Query and validate without publishing anything."
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=CAMPAIGN_LIST_LIMIT,
            help=(
                f"How many completed campaigns to list (default {CAMPAIGN_LIST_LIMIT}). "
                "Always a number: an unbounded request is not something a "
                "scheduled job asks a shared API for."
            ),
        )
        parser.add_argument(
            "--stats-limit",
            type=int,
            default=MAX_STATS_PER_RUN,
            help=(
                f"How many campaigns may have their statistics read this run "
                f"(default {MAX_STATS_PER_RUN}). Each is one paced request. "
                "Raise it for a one-off historical backfill."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]

        if options["limit"] < 1:
            raise CommandError("--limit peab olema vähemalt 1.")
        if options["stats_limit"] < 0:
            raise CommandError("--stats-limit ei saa olla negatiivne.")

        try:
            with advisory_lock(LOCK_NAME):
                outcome = synchronize_campaigns(
                    dry_run=options["dry_run"],
                    limit=options["limit"],
                    stats_limit=options["stats_limit"],
                )
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
        response — and no campaign list either. A scheduler log is not where the
        Chamber's subject lines belong.

        Every key is named here rather than copied out of `outcome.extra`, so
        the JSON a scheduler parses cannot gain or lose fields depending on how
        the run ended.
        """
        return {
            "result": outcome.result,
            "detail": outcome.detail,
            "dry_run": outcome.dry_run,
            "campaigns_listed": outcome.extra.get("campaigns_listed", 0),
            "campaigns_catalogued": outcome.extra.get("campaigns_catalogued", 0),
            "campaigns_updated": outcome.extra.get("campaigns_updated", 0),
            "campaigns_unclassified": outcome.extra.get("campaigns_unclassified", 0),
            "stats_examined": outcome.extra.get("stats_examined", 0),
            "stats_imported": outcome.extra.get("stats_imported", 0),
            "stats_revised": outcome.extra.get("stats_revised", 0),
            "stats_unchanged": outcome.extra.get("stats_unchanged", 0),
            "api_requests": outcome.extra.get("api_requests", 0),
            "api_retries": outcome.extra.get("api_retries", 0),
        }
