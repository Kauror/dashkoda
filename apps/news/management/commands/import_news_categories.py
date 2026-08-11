"""One-time import of whose news each catalogued article is.

    python manage.py import_news_categories --file <path.csv> --dry-run --json
    python manage.py import_news_categories --file <path.csv> --json

Koda.ee stores this on every news node as `field_category`, and **nothing
public exposes it**: the article page carries no marker, the RSS feed emits no
`<category>`, JSON:API is disabled and the archive view accepts no category
filter. The two category listing pages do show it and are how newly published
articles are classified, but they are capped at roughly the most recent year.

So the history comes from a file — a read of the site's own admin, or an export
from whoever maintains it. The file is data, not code: it is never committed,
and this command is the only thing that reads it.

The CSV needs two columns, in any order, with any other columns ignored: one
naming the article by URL or path, one naming its Koda.ee category value. Rows
whose path the catalogue does not hold are counted and skipped — this fills in a
fact about articles DashKoda already knows, and a stale export must not be able
to resurrect an article the site has removed.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.news.catalogue import record_categories

#: Column names accepted for each of the two things needed. Matched
#: case-insensitively, so an export's own header does not have to be edited.
PATH_COLUMNS = ("path", "url", "canonical_url", "aadress", "viide", "tee")
CATEGORY_COLUMNS = ("category", "kategooria", "field_category", "liik")


def _column(fieldnames, candidates) -> str:
    lowered = {name.strip().lower(): name for name in fieldnames or ()}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return ""


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Import the Koja/Sõprade category for catalogued news articles from a CSV."

    def add_arguments(self, parser):
        self.add_output_arguments(parser, dry_run_help="Read and match without writing.")
        parser.add_argument("--file", required=True, help="Path to the CSV mapping.")

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"Faili ei leitud: {path}")

        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            url_column = _column(reader.fieldnames, PATH_COLUMNS)
            category_column = _column(reader.fieldnames, CATEGORY_COLUMNS)
            if not url_column or not category_column:
                raise CommandError(
                    "CSV vajab veergu aadressiga ("
                    + ", ".join(PATH_COLUMNS)
                    + ") ja veergu kategooriaga ("
                    + ", ".join(CATEGORY_COLUMNS)
                    + ")."
                )
            rows = [(row.get(url_column, ""), row.get(category_column, "")) for row in reader]

        updated, unchanged, unknown = record_categories(rows, dry_run=options["dry_run"])
        payload = {
            "dry_run": options["dry_run"],
            "rows": len(rows),
            "updated": updated,
            "unchanged": unchanged,
            "unknown": unknown,
        }
        verb = "salvestataks" if options["dry_run"] else "salvestatud"
        message = (
            f"{updated} uudise liik {verb}, {unchanged} oli juba õige, "
            f"{unknown} rida ei sobinud ({len(rows)} rida failis)."
        )

        self.emit(options["as_json"], payload, message, style=self.style.SUCCESS)
