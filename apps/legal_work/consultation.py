"""When a legal-work record may carry a consultation link, stated once.

A consultation page — a Koda.ee `Hetkel käsil` entry, current or archived — is an
invitation to comment on a draft. It is the right thing to link from a legal
topic only while that invitation is still the record's live business: the matter
is in work and the Chamber has not yet sent its opinion.

Two conditions, and both are about the **workbook**, never about the matcher:

- ``is_open`` is true — the matter has not been concluded;
- ``sent_status`` is not ``sent`` — no opinion has gone out.

Once an opinion has been sent, the consultation page is history. What a reader
wants then is the opinion itself, and DashKoda does not have it yet: opinions,
news items and PDFs are a later resource pipeline. Until it exists, a sent record
renders as **plain text** rather than pointing back at a consultation the Chamber
has already answered. That is a deliberate gap, not an oversight.

The rule lives here, once, because it is consumed by four separate query paths —
current matching, archive matching, viewer link resolution and the selectors —
and a business rule spelled out four times is a business rule that will one day
mean four different things.
"""

from __future__ import annotations

from django.db.models import Q

from .models import SentStatus

# The one definition. Everything that asks "may this record carry a consultation
# link?" composes this rather than restating the two conditions.
CONSULTATION_ELIGIBLE = Q(is_open=True) & ~Q(sent_status=SentStatus.SENT)


def is_consultation_eligible(item) -> bool:
    """The same rule for a single in-memory row.

    Kept beside the `Q` so the two can never drift; a test asserts they agree
    over every combination of the two fields.
    """
    return bool(item.is_open) and item.sent_status != SentStatus.SENT


def consultation_eligible_items(queryset):
    """Narrow any `LegalWorkItem` queryset to the consultation-eligible rows."""
    return queryset.filter(CONSULTATION_ELIGIBLE)
