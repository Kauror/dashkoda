"""Resolving the automatic public address for a legal-work record.

This is the only place where a match result becomes something a viewer can
click, and it is deliberately a **read path over already-published data**: it
runs one bounded query against PostgreSQL, contacts nothing, and can be called
during a page render because it does no more work than any other selector.

The rule it enforces is exact-current-snapshot agreement. A link is offered only
when the current match snapshot was computed from *the very rows being
displayed* — the current legal snapshot and the current catalogue — and the
decision for that record is `matched`. Every other situation renders plain text.

That strictness is the point. The alternative failure is a link that points at
last week's consultation because the workbook moved on overnight and matching
has not run yet, and a lawyer sent to the wrong consultation is worse off than
one sent nowhere. **A stale match is no match.**

Nothing here mutates a model instance or attaches an attribute to one. The
result is a plain mapping of legal-item id to URL, and the presentation objects
built from it are frozen.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import F

from apps.core.public_http import is_allowed_public_url

from .models import (
    MAX_CANONICAL_URL_LENGTH,
    LegalCurrentTopicMatch,
    LegalWorkItem,
    MatchDecision,
)


def is_publishable_topic_url(url: str) -> bool:
    """Whether a stored candidate address may be rendered as a link.

    The collector already validated this URL before storing it, and this checks
    it again anyway. The two validations guard different moments: the collector
    guards what enters the database, and this guards what leaves it, which also
    covers a row written before a rule tightened. It is a handful of string
    operations, so paying for it on every render costs nothing.

    Deliberately **no availability check**. Whether the page still responds is
    not knowable without a request, and a page render never makes one.
    """
    if not url or len(url) > MAX_CANONICAL_URL_LENGTH:
        return False
    # HTTPS on an exact allowlisted Koda.ee host.
    if not is_allowed_public_url(url, allowed_hosts=settings.KODA_ALLOWED_HOSTS):
        return False

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    # `hostname` quietly ignores credentials, so a URL carrying them passes the
    # host check above. They have no business in a public address and their
    # presence is the shape of a phishing link, so they are refused here.
    if parts.username or parts.password:
        return False

    path = parts.path.rstrip("/")
    if path == urlsplit(settings.KODA_CURRENT_TOPICS_URL).path.rstrip("/"):
        return False
    if path == settings.KODA_CURRENT_TOPICS_ARCHIVE_PATH.rstrip("/"):
        return False
    return f"{path}/".startswith(settings.KODA_CURRENT_TOPICS_PATH_PREFIX)


def resolve_topic_links(item_ids) -> dict[int, str]:
    """Map displayed legal-item ids onto their eligible public addresses.

    One query for the whole page, however many records it draws, so adding a row
    to a list never adds a query. Records with no eligible link are simply
    absent from the mapping.

    Nine of the ten eligibility conditions are expressed in the query itself,
    including the two that catch staleness: the displayed row must belong to the
    same legal snapshot the match was computed from, and the candidate must
    belong to the same catalogue snapshot. Writing those as `F()` comparisons
    rather than as Python checks means a stale pair can never be *fetched*, let
    alone rendered. The tenth condition is the URL, checked below.
    """
    ids = {int(item_id) for item_id in item_ids}
    if not ids:
        return {}

    rows = LegalCurrentTopicMatch.objects.filter(
        legal_item_id__in=ids,
        # Only a high-confidence automatic decision is publishable. An ambiguous
        # front-runner is recorded for calibration and is never a link.
        decision=MatchDecision.MATCHED,
        best_candidate__isnull=False,
        # The three snapshots that must all be the current ones.
        snapshot__is_current=True,
        snapshot__legal_snapshot__is_current=True,
        snapshot__current_topic_snapshot__is_current=True,
        # ...and must be the *same* ones this row was computed from.
        legal_item__snapshot=F("snapshot__legal_snapshot"),
        best_candidate__snapshot=F("snapshot__current_topic_snapshot"),
    ).values_list("legal_item_id", "best_candidate__canonical_url")

    return {item_id: url for item_id, url in rows.iterator() if is_publishable_topic_url(url)}


@dataclass(frozen=True)
class LegalTopicPresentation:
    """One legal-work record together with its resolved public address.

    Frozen, and holding the imported row rather than standing in for it: the
    record itself stays exactly the object the importer wrote, and the address
    is a separate value that the read path worked out. Nothing is assigned onto
    a model instance, so no view can accidentally persist a presentation
    decision.

    `topic` and `public_url` are what the shared `legal_topic` component reads,
    which is the whole of its documented contract; everything else a template
    needs is reached through `.item`.
    """

    item: LegalWorkItem
    public_url: str = ""

    @property
    def topic(self) -> str:
        return self.item.topic

    @property
    def is_linked(self) -> bool:
        return bool(self.public_url)


@dataclass(frozen=True)
class DeadlinePresentation:
    """One approaching deadline whose topic has already been resolved.

    Flat rather than wrapping a `Deadline`, because the template needs the
    urgency wording and the topic side by side and a two-level `.deadline.item`
    path in a template is how a render site ends up reading the wrong object.
    """

    topic: LegalTopicPresentation
    days_remaining: int
    variant: str
    remaining_label: str

    @property
    def deadline_date(self):
        return self.topic.item.deadline_date


def resolve_links_for(*groups) -> dict[int, str]:
    """One link mapping for every record a page is about to draw.

    Takes the page's collections — a record may legitimately appear in more than
    one — and resolves them together. That is what makes the same record link to
    the same address in every list it appears in: there is one lookup and one
    answer, not one lookup per list.

    Accepts iterables of `LegalWorkItem` and of anything exposing `.item`, which
    is the shape of the deadline wrapper.
    """
    ids: set[int] = set()
    for group in groups:
        for entry in group:
            ids.add(getattr(entry, "item", entry).pk)
    return resolve_topic_links(ids)


def present_topics(items, links: dict[int, str]) -> tuple[LegalTopicPresentation, ...]:
    """Wrap records for rendering, using an already-resolved mapping."""
    return tuple(
        LegalTopicPresentation(item=item, public_url=links.get(item.pk, "")) for item in items
    )


def present_deadlines(deadlines, links: dict[int, str]) -> tuple[DeadlinePresentation, ...]:
    return tuple(
        DeadlinePresentation(
            topic=LegalTopicPresentation(
                item=deadline.item, public_url=links.get(deadline.item.pk, "")
            ),
            days_remaining=deadline.days_remaining,
            variant=deadline.variant,
            remaining_label=deadline.remaining_label,
        )
        for deadline in deadlines
    )
