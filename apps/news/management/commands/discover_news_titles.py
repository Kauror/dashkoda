"""Name the articles that scrolled out of the feed before DashKoda watched.

Run by hand, repeatedly, until it reports nothing left to do. Every run takes
the most-viewed still-unnamed news pages, reads their titles off Koda.ee and
catalogues them; running it again continues where the last one stopped.

    python manage.py discover_news_titles --json
    python manage.py discover_news_titles --limit 300 --json

Not scheduled. The `sync_koda_public` news feed catalogues everything published
from now on, so this is a one-time backfill of history rather than an ongoing
job — and a nightly crawl of the Chamber's own site to re-read titles nobody
changed would be rude to a server for no benefit.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.news.discovery import MAX_PAGES_PER_RUN, discover_news_titles


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Read titles for news pages the catalogue cannot name, busiest first."

    def add_arguments(self, parser):
        self.add_output_arguments(
            parser, dry_run_help="Fetch and parse without cataloguing anything."
        )
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

        tally = discover_news_titles(limit=options["limit"], dry_run=options["dry_run"])
        payload = {"dry_run": options["dry_run"], **tally.as_dict()}
        message = (
            f"{tally.named} pealkirja lisatud, {tally.unnamed} lehelt ei leitud, "
            f"{tally.failed} ebaõnnestus ({tally.considered} kandidaati)."
        )
        self.emit(options["as_json"], payload, message, style=self.style.SUCCESS)
