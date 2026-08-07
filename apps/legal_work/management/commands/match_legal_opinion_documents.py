"""Match sent legal-work records against the private opinion catalogue.

Reads the current legal snapshot and the current opinion catalogue, decides
which document answered which record, and publishes one immutable match
snapshot. Identical inputs — the same two snapshots and the same matcher
version — return `unchanged` without recomputing.

A `matched` decision is what makes a sent topic clickable. `ambiguous` and
`unmatched` render as plain text, which is the deliberate answer when the
evidence is not conclusive: a lawyer sent to the wrong opinion is worse off than
one sent nowhere.

There is no approve, reject, override or force-match anywhere. Corrections are
made to the rules in `opinion_matching.py`, released as a new matcher version,
and re-run.

Exit codes:

    0  generated, unchanged, skipped, or a successful dry run
    1  failed
    3  another matching run was already in progress
"""

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.core.feeds import FeedLocked, advisory_lock
from apps.legal_work.opinion_match_sync import (
    LOCK_NAME,
    RESULT_FAILED,
    RESULT_GENERATED,
    run_opinion_matching,
)
from apps.legal_work.sync import EXIT_FAILED


class Command(FeedCommandOutputMixin, BaseCommand):
    help = (
        "Match opinion-eligible legal-work records against the current private "
        "opinion catalogue and publish an immutable match snapshot."
    )

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Score every record without publishing a match snapshot."
        )

    def handle(self, *args, **options):
        as_json = options["as_json"]
        try:
            with advisory_lock(LOCK_NAME):
                report = run_opinion_matching(dry_run=options["dry_run"])
        except FeedLocked as error:
            self.exit_locked(error, as_json=as_json)

        # Counts, a snapshot id and the matcher version. Never a topic, a
        # filename, a recipient, a subject, document text or a path.
        payload = report.as_dict()
        if report.result == RESULT_FAILED:
            self.emit(as_json, payload, report.detail, style=self.style.ERROR)
            raise SystemExit(EXIT_FAILED)

        message = report.detail
        if report.result == RESULT_GENERATED:
            message = (
                f"{report.detail} Põhidokumente: {report.primary_relations}, "
                f"kaasdokumente: {report.secondary_relations}."
            )
        self.emit(as_json, payload, message, style=self.style.SUCCESS)
