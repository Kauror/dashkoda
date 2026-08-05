"""Scheduled collection of the public Koda.ee `Hetkel käsil` catalogue.

Safe to run from the host's scheduler: overlapping runs are refused by this
feed's own PostgreSQL advisory lock, the canonical checksum decides whether
anything changed, and any failure leaves the previously published catalogue
exactly where it was.

There is deliberately **no `--url` option**. The listing address is fixed
configuration on an exact host allowlist, and the only pages reached are the
ones that listing links to. Nothing a viewer or an administrator can type
becomes a request.

Exit codes:

    0  imported, unchanged, or a successful dry run
    1  failed
    3  another collection was already running
"""

import json

from django.core.management.base import BaseCommand

from apps.core.feeds import FeedLocked, FeedResult, advisory_lock
from apps.legal_work.current_topic_sync import LOCK_NAME, synchronize_current_topics
from apps.legal_work.sync import EXIT_FAILED, EXIT_LOCKED


class Command(BaseCommand):
    help = (
        "Collect the public Koda.ee 'Hetkel käsil' listing and the detail pages "
        "it links to, and publish them as an immutable catalogue snapshot."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Collect and validate without publishing a snapshot.",
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
            with advisory_lock(LOCK_NAME):
                outcome = synchronize_current_topics(dry_run=options["dry_run"])
        except FeedLocked as error:
            self._emit(
                as_json,
                {"result": "locked", "detail": str(error)},
                f"Vahele jäetud: {error}",
                style=self.style.WARNING,
            )
            raise SystemExit(EXIT_LOCKED) from None

        payload = self._payload(outcome)
        if outcome.result == FeedResult.FAILED:
            self._emit(as_json, payload, outcome.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        message = outcome.detail
        if outcome.result == FeedResult.IMPORTED:
            message = f"{outcome.detail} Teemasid: {payload['item_count']}."
        self._emit(as_json, payload, message, style=self.style.SUCCESS)

    def _payload(self, outcome) -> dict:
        """The whole JSON contract: aggregates and identifiers, nothing else.

        No topic, no candidate title, no URL, no page text and no HTML — a
        scheduler log is not a place where the source's content belongs.
        """
        return {
            "result": outcome.result,
            "detail": outcome.detail,
            "dry_run": outcome.dry_run,
            "snapshot_id": outcome.extra.get("snapshot_id"),
            "item_count": outcome.extra.get("items", 0),
        }

    def _emit(self, as_json: bool, payload: dict, message: str, *, style) -> None:
        if as_json:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(style(message))
