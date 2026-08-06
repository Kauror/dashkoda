"""When a legal-work record may carry an opinion-resource link, stated once.

The mirror image of `consultation.py`. A consultation page is an invitation to
comment and belongs to a record while the matter is open and unanswered; an
opinion document is the Chamber's reply and belongs to a record only once that
reply has gone out.

Two conditions, and both are about the **workbook**, never about the matcher:

- ``sent_status`` is ``sent`` — an opinion has gone out;
- ``sent_date`` is present — the workbook records when.

The date is not decoration. It is the strongest matching signal available: a
letter carries its own outgoing date and its filename usually carries another,
and a record that cannot say when its opinion was sent cannot be matched with
any confidence. Rather than let such a record be matched on subject similarity
alone — which is exactly how a plausible wrong link gets made — it is excluded
and renders as plain text.

The two rules are exclusive by construction, and a test asserts it: a record
cannot be simultaneously unsent and sent, so no record can ever offer both a
consultation link and an opinion link.
"""

from __future__ import annotations

from django.db.models import Q

from .models import SentStatus

# The one definition. Everything that asks "may this record carry an opinion
# link?" composes this rather than restating the two conditions.
OPINION_ELIGIBLE = Q(sent_status=SentStatus.SENT) & Q(sent_date__isnull=False)


def opinion_eligible_q(prefix: str = "") -> Q:
    """The same rule, addressed through a relation.

    The decision tables reach their record as `legal_item`, so their queries
    need `legal_item__sent_status`. Building the `Q` from the one definition
    keeps the rule single-sourced rather than spelled out per query.
    """
    if not prefix:
        return OPINION_ELIGIBLE
    return Q(**{f"{prefix}sent_status": SentStatus.SENT}) & Q(
        **{f"{prefix}sent_date__isnull": False}
    )


def is_opinion_eligible(item) -> bool:
    """The same rule against an in-memory row."""
    return item.sent_status == SentStatus.SENT and item.sent_date is not None


def opinion_eligible_items(queryset):
    return queryset.filter(OPINION_ELIGIBLE)
