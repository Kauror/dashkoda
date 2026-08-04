"""Publish deterministic synthetic content for the browser acceptance suite.

CI's database is empty, so the browser suite has always exercised empty states
only. That is why a real 152-pixel horizontal overflow reached production while
every viewport assertion passed: nothing in CI was ever long enough to truncate.
This command fills the database with content shaped to expose exactly that class
of defect — very long Estonian titles, linked headings carrying a visually
hidden suffix, wide amounts, explicit zeros beside genuinely missing values, and
enough rows to scroll.

**Every value here is invented.** No Chamber member total, fee figure, event,
legal topic, article, organisation or URL appears. The names are obviously
synthetic so that a screenshot can never be mistaken for real data.

It publishes through the domain services rather than writing rows directly, so
the seeded state is one the application could actually have reached: the same
collectors, importers, import registry, atomic publication and audit trail. No
immutability guard is weakened to make seeding easier, and nothing here performs
a network request or touches a real source.

Re-running is safe. Every publisher is idempotent over its own content identity
— the feed syncs by checksum, the manual publishers by content hash — so a
second run on the same day publishes nothing new and reports `unchanged`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# Settings modules under which seeding is permitted. Production is refused by
# construction rather than by a flag someone can pass anyway.
ALLOWED_SETTINGS_MODULES = frozenset(
    {
        "config.settings.local",
        "config.settings.test",
    }
)

# One long Estonian sentence, reused where a title has to be long enough to
# truncate. Long enough to overflow a narrow card, and unmistakably synthetic.
LONG_TITLE = (
    "Sünteetiline väga pikk pealkiri, mis on kirjutatud ainult selleks, "
    "et kontrollida kärpimist, murdmist ja horisontaalset kerimist kõige "
    "kitsamas vaates, ning see ei kirjelda ühtegi tegelikku Koja tegevust"
)
LONG_TOPIC = (
    "Sünteetiline õigusloome teema, mille pealkiri on tahtlikult äärmiselt pikk, "
    "et kontrollida tabeliveeru kärpimist ja seda, kas pikk seotud pealkiri koos "
    "peidetud lisamärkusega ajab lehe horisontaalselt kerima; ükski sõna siin ei "
    "puuduta tegelikku õigusloomet ega Koja seisukohti"
)
LONG_LOCATION = "Sünteetiline konverentsikeskus, sünteetiline suur saal, sünteetiline aadress 123"
LONG_CATEGORY = "Sünteetiline pikk kategooria nimetus"


def _require_non_production() -> str:
    """Refuse to seed anything but an explicit development or test database."""
    module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if module not in ALLOWED_SETTINGS_MODULES:
        raise CommandError(
            "seed_e2e_data refuses to run under "
            f"{module or '(unset DJANGO_SETTINGS_MODULE)'}. "
            "It is permitted only under " + ", ".join(sorted(ALLOWED_SETTINGS_MODULES)) + "."
        )
    return module


# --------------------------------------------------------------------------
# Legal work — a real workbook through the real parser
# --------------------------------------------------------------------------


def _legal_work_rows(today: dt.date) -> list[list]:
    """DATA rows in canonical column order, deliberately varied.

    Covers a long topic, a topic with no deadline, all three sent statuses, a
    warning code, and enough open rows that the page's list has to scroll.
    """
    from apps.legal_work.workbook import DATA_COLUMNS  # noqa: F401  (column order documented)

    rows: list[list] = []

    def row(
        *,
        index: int,
        topic: str,
        received_offset: int,
        deadline_offset: int | None,
        sent_offset: int | None = None,
        sent_status: str = "pending",
        is_open: bool = True,
        warning_codes: str | None = None,
        stage: str = "sünteetiline menetlusetapp",
    ) -> list:
        return [
            f"SEED-{index:04d}",
            2099,
            index,
            topic,
            "sünteetiline õigusakti liik",
            today - dt.timedelta(days=received_offset),
            None if deadline_offset is None else today + dt.timedelta(days=deadline_offset),
            None if sent_offset is None else today - dt.timedelta(days=sent_offset),
            sent_status,
            "Sünteetiline saaja ministeerium",
            stage,
            stage,
            "sünteetiline järgmine samm",
            is_open,
            warning_codes,
            index + 1,
            dt.datetime.combine(today - dt.timedelta(days=1), dt.time(6, 30)),
        ]

    # The overflow candidate: an extremely long topic in a linked table cell.
    rows.append(row(index=1, topic=LONG_TOPIC, received_offset=40, deadline_offset=2))
    # Deadlines across the urgency thresholds the selectors use.
    rows.append(
        row(
            index=2,
            topic="Sünteetiline kiireloomuline teema",
            received_offset=30,
            deadline_offset=1,
        )
    )
    rows.append(
        row(index=3, topic="Sünteetiline lähituleviku teema", received_offset=25, deadline_offset=8)
    )
    rows.append(
        row(index=4, topic="Sünteetiline rahulik teema", received_offset=20, deadline_offset=18)
    )
    # Open, but with no deadline at all: must sort last, never first.
    rows.append(
        row(index=5, topic="Sünteetiline tähtajata teema", received_offset=15, deadline_offset=None)
    )
    # A warning code, and an empty stage, which is what produces `missing_stage`.
    rows.append(
        row(
            index=6,
            topic="Sünteetiline hoiatusega teema",
            received_offset=12,
            deadline_offset=5,
            warning_codes="missing_stage",
            stage="",
        )
    )
    # Sent opinions, so "viimati välja läinud" has content.
    for offset, index in enumerate(range(7, 13)):
        rows.append(
            row(
                index=index,
                topic=f"Sünteetiline saadetud arvamus {index}",
                received_offset=60 + offset,
                deadline_offset=None,
                sent_offset=5 + offset,
                sent_status="sent",
                is_open=False,
                stage="jõustunud",
            )
        )
    # Explicitly not sent: distinct from pending, and carries no date.
    rows.append(
        row(
            index=13,
            topic="Sünteetiline saatmata jäetud teema",
            received_offset=70,
            deadline_offset=None,
            sent_status="not_sent",
            is_open=False,
            stage="lõpetatud",
        )
    )
    # Enough remaining open rows that the bounded list actually scrolls.
    for index in range(14, 25):
        rows.append(
            row(
                index=index,
                topic=f"Sünteetiline töös olev teema {index}",
                received_offset=index,
                deadline_offset=index,
            )
        )
    return rows


def _write_legal_work_workbook(path: Path, today: dt.date) -> Path:
    """Write a workbook that satisfies the canonical contract exactly."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table

    from apps.legal_work.workbook import (
        CONTROL_SHEET,
        DATA_COLUMNS,
        DATA_SHEET,
        DATA_TABLE_NAME,
        OVERVIEW_SHEET,
        WARNINGS_SHEET,
    )

    rows = _legal_work_rows(today)
    is_open_index = DATA_COLUMNS.index("is_open")
    sent_status_index = DATA_COLUMNS.index("sent_status")
    warnings_index = DATA_COLUMNS.index("warning_codes")
    reporting_date = today - dt.timedelta(days=1)
    generated_at = dt.datetime.combine(reporting_date, dt.time(6, 30))

    control = {
        "dataset_key": "oigusloome",
        "schema_version": "1.1",
        "source_file_name": "sünteetiline-lähtefail.xlsx",
        "source_sheet": "2099",
        "source_modified_at": "",
        "source_sha256": "0" * 64,
        "generated_at": generated_at.isoformat(),
        "reporting_date": reporting_date,
        "total_record_count": len(rows),
        "open_record_count": sum(1 for row in rows if row[is_open_index] is True),
        "sent_record_count": sum(1 for row in rows if row[sent_status_index] == "sent"),
        "not_sent_record_count": sum(1 for row in rows if row[sent_status_index] == "not_sent"),
        "warning_record_count": sum(1 for row in rows if row[warnings_index]),
        "refresh_status": "completed_with_warnings",
        "generator_version": "seed-e2e",
    }

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name in (CONTROL_SHEET, OVERVIEW_SHEET, DATA_SHEET, WARNINGS_SHEET):
        workbook.create_sheet(name)

    control_sheet = workbook[CONTROL_SHEET]
    control_sheet.append(["DASHKODA ÕIGUSLOOME ANDMEVOOG (SÜNTEETILINE)", None])
    for key, value in control.items():
        control_sheet.append([key, value])

    workbook[OVERVIEW_SHEET].append(["Sünteetiline ülevaade"])

    data = workbook[DATA_SHEET]
    data.append(list(DATA_COLUMNS))
    for row in rows:
        data.append(row)
    last_column = chr(ord("A") + len(DATA_COLUMNS) - 1)
    data.add_table(Table(displayName=DATA_TABLE_NAME, ref=f"A1:{last_column}{len(rows) + 1}"))

    warnings_sheet = workbook[WARNINGS_SHEET]
    warnings_sheet.append(["record_id", "source_row", "field", "warning_code"])
    for row in rows:
        if not row[warnings_index]:
            continue
        for code in str(row[warnings_index]).split(";"):
            warnings_sheet.append([row[0], row[-2], "stage", code.strip()])
    warnings_sheet.add_table(
        Table(displayName="tbl_oigusloome_warnings", ref=f"A1:D{warnings_sheet.max_row}")
    )

    workbook.save(path)
    return path


def _seed_legal_work(today: dt.date) -> str:
    from apps.legal_work.public_download import XLSX_MIME_TYPE, PublicDownload
    from apps.legal_work.public_sync import synchronize_public_workbook
    from apps.legal_work.sync import SyncLocked, advisory_lock

    def downloader(destination: Path) -> PublicDownload:
        _write_legal_work_workbook(destination, today)
        payload = destination.read_bytes()
        return PublicDownload(
            path=destination,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type=XLSX_MIME_TYPE,
            final_host="synthetic-seed.invalid",
        )

    try:
        with advisory_lock():
            outcome = synchronize_public_workbook(downloader=downloader)
    except SyncLocked as error:
        raise CommandError(f"Legal-work seed could not take the lock: {error}") from error
    return f"õigusloome: {outcome.result} ({outcome.rows_imported} kirjet)"


# --------------------------------------------------------------------------
# Public Koda.ee feeds — through their own synchronisation services
# --------------------------------------------------------------------------


def _seed_events(today: dt.date) -> str:
    from apps.core.canonical import canonical_checksum
    from apps.events.collector import EventCollection, EventEntry
    from apps.events.sync import synchronize_events

    entries: list[EventEntry] = []

    def add(index: int, *, title: str, starts_on: dt.date, ends_on: dt.date | None = None) -> None:
        entries.append(
            EventEntry(
                stable_key=f"seed-event-{index}",
                title=title,
                canonical_url=f"https://www.koda.ee/et/sundmused/sunteetiline-{index}",
                category=LONG_CATEGORY if index % 3 == 0 else "Sünteetiline koolitus",
                summary="",
                starts_on=starts_on,
                ends_on=ends_on,
                starts_at=None,
                ends_at=None,
                location=LONG_LOCATION if index % 4 == 0 else "Sünteetiline saal",
                source_order=index,
            )
        )

    # The overflow candidate: a very long linked title carrying the visually
    # hidden "(avaneb koda.ee lehel)" suffix.
    add(1, title=LONG_TITLE, starts_on=today + dt.timedelta(days=2))
    # A multi-day range, so the date column renders two dates.
    add(
        2,
        title="Sünteetiline mitmepäevane sündmus",
        starts_on=today + dt.timedelta(days=5),
        ends_on=today + dt.timedelta(days=7),
    )
    # Month and year boundaries, where date formatting most often breaks.
    first_next_month = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    add(3, title="Sünteetiline kuupiiri sündmus", starts_on=first_next_month - dt.timedelta(days=1))
    add(4, title="Sünteetiline uue kuu sündmus", starts_on=first_next_month)
    add(5, title="Sünteetiline aastavahetuse sündmus", starts_on=dt.date(today.year, 12, 31))
    add(6, title="Sünteetiline uue aasta sündmus", starts_on=dt.date(today.year + 1, 1, 2))
    # Enough further events that the list scrolls.
    for index in range(7, 19):
        add(
            index, title=f"Sünteetiline sündmus {index}", starts_on=today + dt.timedelta(days=index)
        )

    entries.sort(key=lambda item: (item.starts_on, item.title, item.stable_key))
    entries = [
        EventEntry(**{**vars(entry), "source_order": position})
        for position, entry in enumerate(entries)
    ]
    canonical = {
        "dataset": "koda-public-events",
        "schema_version": "1.0",
        "items": [
            {
                "key": entry.stable_key,
                "title": entry.title,
                "url": entry.canonical_url,
                "category": entry.category,
                "starts_on": entry.starts_on,
                "ends_on": entry.ends_on,
                "starts_at": entry.starts_at,
                "ends_at": entry.ends_at,
                "location": entry.location,
            }
            for entry in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    collection = EventCollection(
        entries=tuple(entries),
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        pages_fetched=1,
        details_fetched=len(entries),
        skipped_non_events=0,
        skipped_past=0,
    )
    outcome = synchronize_events(collector=lambda **_kwargs: collection)
    return f"sündmused: {outcome.result} ({len(entries)} sündmust)"


def _seed_news(today: dt.date) -> str:
    from apps.core.canonical import canonical_checksum
    from apps.news.collector import NewsCollection, NewsEntry
    from apps.news.sync import synchronize_news

    midnight = dt.datetime.combine(today, dt.time(9, 0), tzinfo=dt.UTC)
    entries: list[NewsEntry] = []
    for index in range(1, 13):
        entries.append(
            NewsEntry(
                guid=f"seed-news-{index}",
                title=LONG_TITLE if index == 1 else f"Sünteetiline uudise pealkiri {index}",
                canonical_url=f"https://www.koda.ee/et/uudised/sunteetiline-{index}",
                published_at=midnight - dt.timedelta(days=index),
                category=LONG_CATEGORY if index % 4 == 0 else "Sünteetiline rubriik",
                summary=(
                    "Sünteetiline kokkuvõte, mis on piisavalt pikk, et kontrollida "
                    "teksti murdmist ja kärpimist kaardi laiuses."
                ),
                source_order=index - 1,
            )
        )
    canonical = {
        "dataset": "koda-public-news",
        "schema_version": "1.0",
        "items": [
            {
                "guid": entry.guid,
                "title": entry.title,
                "url": entry.canonical_url,
                "published_at": entry.published_at,
                "category": entry.category,
                "summary": entry.summary,
            }
            for entry in entries
        ],
    }
    checksum, size = canonical_checksum(canonical)
    collection = NewsCollection(
        entries=tuple(entries),
        sha256=checksum,
        size_bytes=size,
        canonical=canonical,
        etag="",
        last_modified="",
    )
    outcome = synchronize_news(collector=lambda **_kwargs: collection)
    return f"uudised: {outcome.result} ({len(entries)} uudist)"


def _seed_public_membership() -> str:
    from apps.core.canonical import canonical_checksum
    from apps.membership.collector import MembershipCollection
    from apps.membership.sync import synchronize_membership

    def collection(total: int) -> MembershipCollection:
        canonical = {
            "dataset": "koda-public-members",
            "schema_version": "1.0",
            "total_members": total,
        }
        checksum, size = canonical_checksum(canonical)
        return MembershipCollection(
            total_members=total,
            sha256=checksum,
            size_bytes=size,
            canonical=canonical,
            etag="",
            last_modified="",
            duplicate_identities=0,
            rejected_rows=0,
        )

    # Two readings, so the directory count has a predecessor to compare with.
    # Both are four-digit, which is what makes grouped-number formatting visible.
    results = []
    for total in (4187, 4203):
        outcome = synchronize_membership(collector=lambda total=total, **_kwargs: collection(total))
        results.append(outcome.result)
    return f"liikmed (avalik): {results[-1]} (4203)"


# --------------------------------------------------------------------------
# Internal board-report history — through the manual publication service
# --------------------------------------------------------------------------


def _seed_internal_membership(today: dt.date) -> str:
    from apps.membership.manual import ManualReport, publish_manual_report
    from apps.membership.quality import MetricFacts

    # Six readings across roughly a year, so both overview trend lines have
    # enough points to draw. Values move plausibly and are all invented.
    plan = [
        (330, 4050, 3810, "1180000.00", "1300000.00", 210, 41, 12),
        (270, 4090, 3860, "1205500.00", "1300000.00", 260, 38, 18),
        (210, 4120, 3905, "1231000.50", "1300000.00", 300, 35, 24),
        (150, 4150, 3950, "1252750.25", "1310000.00", 340, 0, 31),
        (90, 4176, 3988, "1268400.00", "1310000.00", 372, 29, 37),
        (30, 4203, 4025, "1276101.00", "1310000.00", 401, None, 44),
    ]
    published = 0
    for offset, total, paid, received, budget, new_ytd, suspended, removed in plan:
        report = ManualReport(
            observation_date=today - dt.timedelta(days=offset),
            reported_year=(today - dt.timedelta(days=offset)).year,
            document_title="Sünteetiline juhatuse aruanne",
            source_note="Sünteetiline seeme, mitte tegelik aruanne.",
            facts=MetricFacts(
                total_members=total,
                paid_members=paid,
                membership_fees_received_eur=Decimal(received),
                membership_fee_budget_eur=Decimal(budget),
                # Left unreported on purpose: the page then shows the computed
                # percentage and says which basis it used.
                membership_fee_collection_pct_reported=None,
                new_members_ytd=new_ytd,
                # `0` on one reading and `None` on another, so the interface has
                # to distinguish "nobody was suspended" from "nobody counted".
                suspended_members=suspended,
                removed_members_ytd=removed,
            ),
        )
        publish_manual_report(report)
        published += 1
    return f"liikmeskonna aruanded (sisemine): {published} vaatlust"


# --------------------------------------------------------------------------
# Visibility — through the manual submission service
# --------------------------------------------------------------------------


def _seed_visibility(today: dt.date) -> str:
    from apps.visibility.manual import VisibilitySubmission, publish_submission
    from apps.visibility.registry import VisibilityMetric

    # Three readings, so every channel card has a trend and a change to state.
    # The oldest deliberately omits two metrics: a channel nobody has read yet
    # must show "andmed puuduvad" rather than a zero.
    plan = [
        (
            120,
            {
                VisibilityMetric.NEWSLETTER_ETEATAJA: 8120,
                VisibilityMetric.NEWSLETTER_ENEWS: 3040,
                VisibilityMetric.NEWSLETTER_EVESTNIK: 1210,
                VisibilityMetric.FACEBOOK_FOLLOWERS: 5210,
                VisibilityMetric.LINKEDIN_FOLLOWERS: 4130,
            },
        ),
        (
            60,
            {
                VisibilityMetric.NEWSLETTER_ETEATAJA: 8260,
                VisibilityMetric.NEWSLETTER_ENEWS: 3105,
                VisibilityMetric.NEWSLETTER_EVESTNIK: 1188,
                VisibilityMetric.FACEBOOK_FOLLOWERS: 5344,
                VisibilityMetric.LINKEDIN_FOLLOWERS: 4402,
                VisibilityMetric.INSTAGRAM_FOLLOWERS: 1870,
                VisibilityMetric.YOUTUBE_SUBSCRIBERS: 640,
            },
        ),
        (
            7,
            {
                VisibilityMetric.NEWSLETTER_ETEATAJA: 8395,
                VisibilityMetric.NEWSLETTER_ENEWS: 3162,
                VisibilityMetric.NEWSLETTER_EVESTNIK: 1174,
                VisibilityMetric.FACEBOOK_FOLLOWERS: 5498,
                VisibilityMetric.LINKEDIN_FOLLOWERS: 4655,
                VisibilityMetric.INSTAGRAM_FOLLOWERS: 1994,
                VisibilityMetric.YOUTUBE_SUBSCRIBERS: 671,
            },
        ),
    ]
    for offset, values in plan:
        publish_submission(
            VisibilitySubmission(
                observation_date=today - dt.timedelta(days=offset),
                values={str(key): value for key, value in values.items()},
                note="Sünteetiline seeme.",
            )
        )
    return f"nähtavus: {len(plan)} sisestust"


# --------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Publish deterministic synthetic content for the browser acceptance "
        "suite. Refuses to run under production settings."
    )

    def handle(self, *args, **options):
        module = _require_non_production()
        today = timezone.localdate()

        # The legal-work synchronisation owns its own temporary directory and
        # deletes it on every exit path, so no seeded workbook outlives the
        # command.
        lines = [
            _seed_legal_work(today),
            _seed_events(today),
            _seed_news(today),
            _seed_public_membership(),
            _seed_internal_membership(today),
            _seed_visibility(today),
        ]

        self.stdout.write(self.style.SUCCESS(f"Sünteetiline seeme ({module}):"))
        for line in lines:
            self.stdout.write(f"  {line}")
