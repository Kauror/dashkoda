"""What GA4 history DashKoda actually holds, and whether collection is working.

The command an operator runs first: before a backfill, to see what is missing;
after one, to see what landed; and when the dashboard looks wrong, to find out
whether the schedule has been failing quietly.

Reads the database and the settings. **Makes no Google request**, so it answers
the same way when the credential is missing, expired or revoked — which is
exactly when someone is running it.

Prints whether GA4 is configured, never *what* it is configured with: no
property ID, no credential path. Both are operational detail this command has no
reason to put on a terminal or in a log.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand

from apps.core.feed_commands import FeedCommandOutputMixin
from apps.visibility.ga4 import get_configuration
from apps.visibility.ga4_selectors import get_coverage, missing_dates
from apps.visibility.ga4_sync import RECONCILIATION_DAYS, reconciliation_window
from apps.visibility.models import Ga4DailySnapshot, Ga4FeedState

#: How many missing dates are named before the list is summarised. A five-year
#: gap would otherwise print 1 800 lines into a terminal.
MAX_NAMED_GAPS = 10


class Command(FeedCommandOutputMixin, BaseCommand):
    help = "Report stored Google Analytics coverage and the last collection result."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit the report as one JSON object.",
        )

    def handle(self, *args, **options):
        payload = self._report()
        self.emit(
            options["as_json"],
            payload,
            self._lines(payload),
            # A report, not an outcome. Nothing here succeeded or failed, so it
            # is written plain rather than in the success colour a sync uses.
            style=lambda text: text,
        )

    def _report(self) -> dict:
        configuration = get_configuration()
        coverage = get_coverage()
        state = Ga4FeedState.objects.select_related("current_snapshot").first()
        window_start, window_end = reconciliation_window()

        gaps: tuple[dt.date, ...] = ()
        if coverage.has_data:
            gaps = missing_dates(coverage.earliest, coverage.latest)

        return {
            # Whether, never what.
            "configured": configuration.is_configured,
            "missing_settings": list(configuration.missing),
            "earliest_date": coverage.earliest.isoformat() if coverage.earliest else None,
            "latest_date": coverage.latest.isoformat() if coverage.latest else None,
            "days_covered": coverage.days_covered,
            "days_in_span": coverage.span_days,
            "days_missing": len(gaps),
            "missing_dates_sample": [day.isoformat() for day in gaps[:MAX_NAMED_GAPS]],
            "days_with_page_detail": coverage.days_with_pages,
            "page_rows": coverage.page_rows,
            "revisions_total": Ga4DailySnapshot.objects.count(),
            "revisions_superseded": Ga4DailySnapshot.objects.filter(
                is_current_for_date=False
            ).count(),
            "next_window_start": window_start.isoformat(),
            "next_window_end": window_end.isoformat(),
            "reconciliation_days": RECONCILIATION_DAYS,
            "last_result": state.last_result if state else None,
            "last_checked_at": _moment(state.last_checked_at if state else None),
            "last_successful_sync_at": _moment(state.last_successful_sync_at if state else None),
            "last_period_end": (
                state.last_period_end.isoformat() if state and state.last_period_end else None
            ),
            "last_error_summary": (state.last_error_summary if state else "") or "",
        }

    def _lines(self, payload: dict) -> str:
        rows = [
            ("Seadistatud", "jah" if payload["configured"] else "ei"),
            ("Varaseim päev", payload["earliest_date"] or "—"),
            ("Viimane päev", payload["latest_date"] or "—"),
            (
                "Päevi kaetud",
                f"{payload['days_covered']} / {payload['days_in_span']}",
            ),
            ("Päevi puudu", str(payload["days_missing"])),
            ("Lehekaupa päevi", str(payload["days_with_page_detail"])),
            ("Leheridu", str(payload["page_rows"])),
            (
                "Redaktsioone",
                f"{payload['revisions_total']} (asendatud {payload['revisions_superseded']})",
            ),
            (
                "Järgmine aken",
                f"{payload['next_window_start']} … {payload['next_window_end']}",
            ),
            ("Viimane tulemus", payload["last_result"] or "—"),
            ("Viimane edukas", payload["last_successful_sync_at"] or "—"),
        ]
        if payload["missing_settings"]:
            rows.append(("Puuduvad seaded", ", ".join(payload["missing_settings"])))
        if payload["missing_dates_sample"]:
            named = ", ".join(payload["missing_dates_sample"])
            if payload["days_missing"] > len(payload["missing_dates_sample"]):
                named += f" … (+{payload['days_missing'] - len(payload['missing_dates_sample'])})"
            rows.append(("Puuduvad päevad", named))
        if payload["last_error_summary"]:
            rows.append(("Viimane viga", payload["last_error_summary"]))

        width = max(len(label) for label, _ in rows)
        return "\n".join(f"{label.ljust(width)}  {value}" for label, value in rows)


def _moment(value) -> str | None:
    return value.isoformat(timespec="seconds") if value else None
