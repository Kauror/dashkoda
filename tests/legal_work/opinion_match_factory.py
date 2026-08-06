"""Building an opinion catalogue and a matched world for the Phase 2 tests.

Everything here goes through the real publication paths — the real catalogue
build, the real matcher — rather than writing rows directly, so a test that
needs a match also proves the pipeline that produces one still works.
"""

from __future__ import annotations

import datetime as dt

from apps.legal_work.opinion_catalogue_sync import synchronize_opinion_documents

from .opinion_factory import build_zip, opinion_pdf


def letter(
    *,
    date: dt.date,
    recipient: str = "Rahandusministeerium",
    subject: str = "Arvamus maksukorralduse seaduse eelnou kohta",
    reference: str = "4/1",
    body: str = "Kaubanduskoda esitab arvamuse eelnou kohta.",
    document_date: dt.date | None = None,
    pages: int = 1,
) -> tuple[str, bytes]:
    """One synthetic opinion letter and the filename it arrives under.

    `document_date` defaults to the filename date. Passing a different one
    reproduces the real catalogue's commonest shape, where the letter is signed
    the working day after it was drafted.
    """
    inner = document_date or date
    name = f"{date:%Y-%m-%d} - {recipient} - {subject}.pdf"
    payload = opinion_pdf(
        recipient=recipient,
        our_date=f"{inner:%d.%m.%Y}",
        our_reference=reference,
        subject=subject,
        body=body,
        pages=pages,
    )
    return name, payload


def publish_catalogue(source, letters: list[tuple[str, bytes]]):
    """Publish a complete opinion catalogue from the given letters."""
    build_zip(
        {f"Opinions/{name}": payload for name, payload in letters}, path=source / "Opinions.zip"
    )
    return synchronize_opinion_documents()
