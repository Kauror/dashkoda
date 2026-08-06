"""Resolving the automatic public address for a legal-work record.

This is the only place where a match result becomes something a viewer can
click, and it is deliberately a **read path over already-published data**: it
runs one bounded query against PostgreSQL, contacts nothing, and can be called
during a page render because it does no more work than any other selector.

Two sources feed it, in a fixed order. The **current** `Hetkel käsil` listing
answers first; the **archive** answers only for records it could not. A
consultation that is still open is a better destination than the same
consultation filed away, and during the days a page is moving between the two it
can briefly appear in both — so current wins and the archive is asked only about
what is left.

The rule both enforce is exact-current-snapshot agreement. A link is offered only
when the match snapshot was computed from *the very rows being displayed* — and
for the archive, additionally, from the very current-topic match run that
decided the record needed a fallback at all. Every other situation renders plain
text.

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
from django.urls import reverse

from apps.core.public_http import is_allowed_public_url

from .consultation import consultation_eligible_q
from .models import (
    MAX_CANONICAL_URL_LENGTH,
    LegalCurrentTopicMatch,
    LegalWorkItem,
    MatchDecision,
)
from .opinion_eligibility import opinion_eligible_q
from .opinion_match_models import LegalOpinionDocumentRelation
from .opinion_pdf import ValidationStatus


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
        # The record must still be allowed a consultation link at all: open, and
        # with no opinion sent. The matcher already refuses the rest, so this is
        # defence in depth — but it is the check that means a stored decision
        # can never outlive the state that justified it.
        consultation_eligible_q("legal_item__"),
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


def resolve_archive_links(item_ids) -> dict[int, str]:
    """The fallback: addresses for records the current listing could not answer.

    One further bounded query. Every condition that can be stated in SQL is,
    including the three that catch staleness — the displayed row, the candidate
    and the current-topic match run this snapshot deferred to must all still be
    the current ones, and must be the *same* ones this row was computed from.

    Two conditions are specific to the archive and both matter:

    - the candidate must be **hydrated**. An index-only row has an editorial
      headline and no body, and the matcher never scores one; refusing it again
      here means a row that somehow slipped through still cannot be rendered.
    - the record must not already have a current match. That is enforced when
      the snapshot is built, and re-checked by the caller's precedence, but a
      link is cheap to refuse and expensive to get wrong.
    """
    from .models import DetailStatus, LegalArchivedTopicMatch

    ids = {int(item_id) for item_id in item_ids}
    if not ids:
        return {}

    rows = LegalArchivedTopicMatch.objects.filter(
        consultation_eligible_q("legal_item__"),
        legal_item_id__in=ids,
        decision=MatchDecision.MATCHED,
        best_candidate__isnull=False,
        # Only a fully read archive page may be offered to a reader.
        best_candidate__detail_status=DetailStatus.HYDRATED,
        best_candidate__is_present=True,
        # All four snapshots current...
        snapshot__is_current=True,
        snapshot__legal_snapshot__is_current=True,
        snapshot__archived_topic_snapshot__is_current=True,
        snapshot__current_topic_match_snapshot__is_current=True,
        # ...and all of them the ones this row was actually computed from.
        legal_item__snapshot=F("snapshot__legal_snapshot"),
        best_candidate__snapshot=F("snapshot__archived_topic_snapshot"),
        snapshot__current_topic_match_snapshot__legal_snapshot=F("snapshot__legal_snapshot"),
    ).values_list("legal_item_id", "best_candidate__canonical_url")

    return {item_id: url for item_id, url in rows.iterator() if is_publishable_topic_url(url)}


def resolve_opinion_links(item_ids) -> dict[int, str]:
    """The internal resource address for each sent record that has one.

    Every staleness condition is in the query rather than checked afterwards,
    because a check that happens "later" is a check some future caller forgets.
    The `F()` comparisons tie the decision to the exact legal snapshot the row
    being displayed belongs to, and to the exact catalogue the document belongs
    to, so a decision computed against yesterday's import cannot surface.

    A matter whose durable identity collided is excluded here rather than at
    render time: an ambiguous identity must never become an address.
    """
    ids = {int(item_id) for item_id in item_ids}
    if not ids:
        return {}

    rows = (
        LegalOpinionDocumentRelation.objects.filter(
            is_primary=True,
            decision__legal_item_id__in=ids,
            decision__snapshot__is_current=True,
            decision__decision=MatchDecision.MATCHED,
            decision__legal_item__snapshot__is_current=True,
            decision__snapshot__legal_snapshot_id=F("decision__legal_item__snapshot_id"),
            decision__snapshot__opinion_catalogue_snapshot_id=F("entry__snapshot_id"),
            entry__snapshot__is_current=True,
            entry__blob__validation_status=ValidationStatus.VALID,
            decision__matter__has_ambiguous_identity=False,
        )
        .filter(opinion_eligible_q("decision__legal_item__"))
        .values_list("decision__legal_item_id", "decision__matter__resource__public_id")
    )
    return {
        item_id: reverse("opinion-resource", args=[public_id])
        for item_id, public_id in rows
        if public_id is not None
    }


def resolve_consultation_links(item_ids) -> dict[int, str]:
    """One address per record, chosen by what the workbook says about it.

    Two branches rather than one chain of fallbacks, because the choice is a
    property of the record's own status:

        sent            -> opinion resource when matched, else plain text
        open and unsent -> current consultation, then archive, else plain text

    A sent record must never fall through to a consultation. The consultation
    page invites comment on a draft the Chamber has already answered, so showing
    it beside an answered record tells a reader the opposite of the truth. The
    two eligibility rules are mutually exclusive by construction — one requires
    not-sent, the other requires sent — so the branches cannot both answer, and
    a test asserts it.

    Three queries whatever the page draws, and none of them per row.
    """
    ids = {int(item_id) for item_id in item_ids}
    if not ids:
        return {}

    links = resolve_opinion_links(ids)

    remaining = ids - set(links)
    if remaining:
        current = resolve_topic_links(remaining)
        links.update(current)
        still_unanswered = remaining - set(current)
        if still_unanswered:
            links.update(resolve_archive_links(still_unanswered))
    return links


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
    return resolve_consultation_links(ids)


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
