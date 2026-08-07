"""Scheduled collection of one completed day of Google Analytics traffic.

**Not enabled in production.** No property ID, no service-account key and no
schedule are installed; this command exists so that enabling GA4 later is
configuration rather than another architecture. Running it without the two
settings fails with a message naming exactly what is missing, and publishes
nothing.

Safe to run from the host's scheduler once it is enabled: overlapping runs are
refused by this feed's own PostgreSQL advisory lock, the canonical checksum over
the normalised reading decides whether anything changed, and any failure leaves
the previously published observation exactly where it was.

There is deliberately **no `--property` and no `--credentials` option**. Both
come from the environment, so neither can enter shell history or a process
listing, and no argument can redirect the command at a different property.

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
from apps.visibility.ga4_sync import LOCK_NAME, synchronize_ga4


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Collect the previous completed day of Google Analytics website traffic "
        "and publish it as an immutable observation."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Query and validate without publishing an observation."
        )
        parser.add_argument(
            "--date",
            dest="period",
            default=None,
            help=(
                "Reporting day as YYYY-MM-DD. Defaults to the previous completed "
                "day in application time. For catching up after an outage."
            ),
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        period = self._period(options["period"])

        try:
            with advisory_lock(LOCK_NAME):
                outcome = synchronize_ga4(dry_run=options["dry_run"], period=period)
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        payload = self._payload(outcome)
        if outcome.result == FeedResult.FAILED:
            self.emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        self.emit(as_json, payload, outcome.detail, style=self.style.SUCCESS)

    def _period(self, raw: str | None) -> date | None:
        if raw is None:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as error:
            raise CommandError(f"--date peab olema kujul YYYY-MM-DD, saadi: {raw}") from error

    def _payload(self, outcome) -> dict:
        """The whole JSON contract: aggregates and identifiers, nothing else.

        No property ID, no credential path, no token and no part of Google's
        response — and no reading figures either. The session and page-view
        counts belong on the dashboard and in the audit trail, not in a
        scheduler log, which is the rule this command already followed in prose
        and now follows in JSON as well.

        `figures_reported` is the one thing about the values an operator needs:
        a day GA4 has no rows for publishes an observation whose figures are all
        absent, and that success is otherwise indistinguishable from an ordinary
        one.
        """
        return {
            "result": outcome.result,
            "detail": outcome.detail,
            "dry_run": outcome.dry_run,
            "period_end": outcome.extra.get("period_end"),
            "observation_id": outcome.extra.get("observation_id"),
            "figures_reported": outcome.extra.get("figures_reported"),
        }
