"""Synthetic legal-work content, published through the real workbook parser.

A generated XLSX goes through the same importer the OneDrive feed uses, so the
seeded state is one the application could actually have reached.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from django.core.management.base import CommandError

from apps.core.e2e_seed import LONG_TOPIC, freeze_package_timestamps


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


def write_workbook(path: Path, today: dt.date) -> Path:
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
    freeze_package_timestamps(path)
    return path


def seed(today: dt.date) -> str:
    from apps.legal_work.public_download import XLSX_MIME_TYPE, PublicDownload
    from apps.legal_work.public_sync import synchronize_public_workbook
    from apps.legal_work.sync import SyncLocked, advisory_lock

    def downloader(destination: Path) -> PublicDownload:
        write_workbook(destination, today)
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
