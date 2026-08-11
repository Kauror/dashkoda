"""Date the catalogued articles whose publication date is still unknown.

Run by hand, repeatedly, until it reports nothing left to do.

    python manage.py backfill_news_dates --json
    python manage.py backfill_news_dates --limit 400 --json

A separate pass from `discover_news_titles` because the two ask opposite
questions. Naming asks which measured paths are missing from the catalogue;
dating asks which catalogue rows have no date. After the naming backfill ran the
first answer was nothing and the second was three and a half thousand rows —
every article recovered from the public site, catalogued undated because this
application believed the pages carried no publication date. They do, in
schema.org JSON-LD, back to at least 2017.

Not scheduled, for the same reason the title backfill is not: `sync_koda_public`
dates everything published from now on, and re-reading dates nobody changed
would be a nightly crawl of the Chamber's own site for no benefit.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.news.discovery import MAX_PAGES_PER_RUN, backfill_news_dates


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Read publication dates for catalogued news pages that have none."

    def add_arguments(self, parser):
        self.add_output_arguments(parser, dry_run_help="Fetch and parse without writing any date.")
        parser.add_argument(
            "--limit",
            type=int,
            default=MAX_PAGES_PER_RUN,
            help=(
                f"How many pages this run may fetch (default {MAX_PAGES_PER_RUN}). "
                "Each is one polite request with a pause between."
            ),
        )

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit peab olema vähemalt 1.")

        tally = backfill_news_dates(limit=options["limit"], dry_run=options["dry_run"])
        payload = {"dry_run": options["dry_run"], **tally.as_dict()}
        message = (
            f"{tally.named} kuupäeva lisatud, {tally.unnamed} lehel kuupäeva ei olnud, "
            f"{tally.failed} ebaõnnestus ({tally.considered} kandidaati)."
        )
        self.emit(options["as_json"], payload, message, style=self.style.SUCCESS)
