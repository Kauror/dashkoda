"""The complete record of what the Chamber has sent through Smaily.

The Uudised page shows the most recent sends. This is the archive behind them:
3 194 completed campaigns on this account, the oldest from August 2012, and
every one of them reachable by type, by period and by subject.

## Why it is a separate page

Two reasons, and the second is the one that decided it:

- fourteen years of sends is not a section, it is a page. Rendering three
  thousand rows into the Uudised page would make every visit pay for history
  almost nobody is asking for on that visit;
- the Uudised page answers "what have we published and how did it do"; this
  answers "what did we send and when". A reader arrives here already knowing
  which of the two they want.

## Every completed send, and only completed sends

`DRAFT`, `PENDING` and `CANCELLED` never reach the database at all — the
collector asks Smaily for `COMPLETED` and nothing else. What *does* reach it is
every completed campaign whatever its kind, including the 2 105 that match none
of the three newsletters. Those are shown under `Muu`: they are event calendars,
invitations, Christmas cards and export bulletins, and they are all things the
Chamber really posted to real people.

Nothing here contacts Smaily. The subject search runs against the stored name in
PostgreSQL, like every other query a page render makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.core.paginator import Paginator
from django.db.models import Max, Min

from .registry import spec_for
from .smaily_campaigns import OTHER_KEY, OTHER_LABEL
from .smaily_segments import NEWSLETTERS
from .smaily_selectors import (
    MAX_SEARCH_LENGTH,
    CampaignPerformance,
    campaign_queryset,
    describe_campaigns,
    has_unclassified_campaigns,
)

#: Query parameters this page reads.
PARAM_NEWSLETTER = "uudiskiri"
PARAM_SEARCH = "otsi"
PARAM_PAGE = "lk"

#: Rows per page. Enough to scan, few enough that the page stays quick and the
#: table does not become its own scrolling problem on a phone.
PER_PAGE = 50

ALL_NEWSLETTERS = "koik"


def parse_newsletter(raw: str | None) -> str:
    """The type asked for, or all of them. Never raises."""
    value = (raw or "").strip()
    if value == OTHER_KEY or any(spec.metric == value for spec in NEWSLETTERS):
        return value
    return ALL_NEWSLETTERS


def parse_search(raw: str | None) -> str:
    """The search term, trimmed and bounded. Never reaches SQL as SQL."""
    return (raw or "").strip()[:MAX_SEARCH_LENGTH]


@dataclass(frozen=True)
class HistoryOption:
    """One type filter, carrying whatever search is in force."""

    key: str
    label: str
    is_active: bool
    search: str = ""

    @property
    def query(self) -> str:
        query = f"{PARAM_NEWSLETTER}={self.key}"
        if self.search:
            from urllib.parse import quote

            query += f"&{PARAM_SEARCH}={quote(self.search)}"
        return query


@dataclass(frozen=True)
class CampaignHistory:
    """Everything the archive page renders."""

    rows: tuple[CampaignPerformance, ...]
    options: tuple[HistoryOption, ...]
    search: str
    active: str
    page_number: int
    total_pages: int
    total_rows: int
    has_previous: bool
    has_next: bool
    earliest: date | None = None
    latest: date | None = None

    @property
    def is_filtered(self) -> bool:
        return self.active != ALL_NEWSLETTERS or bool(self.search)

    @property
    def has_rows(self) -> bool:
        return bool(self.rows)

    @property
    def previous_page(self) -> int:
        return max(self.page_number - 1, 1)

    @property
    def next_page(self) -> int:
        return min(self.page_number + 1, max(self.total_pages, 1))

    def page_query(self, page: int) -> str:
        query = f"{PARAM_NEWSLETTER}={self.active}&{PARAM_PAGE}={page}"
        if self.search:
            from urllib.parse import quote

            query += f"&{PARAM_SEARCH}={quote(self.search)}"
        return query

    @property
    def summary(self) -> str:
        """What the reader is looking at, in words.

        Stated because a filtered archive that says only "50 rows" leaves the
        reader unable to tell a narrow search from an empty one.
        """
        if not self.total_rows:
            return "Ühtegi saadetud uudiskirja ei leitud."
        span = ""
        if self.earliest and self.latest:
            span = f", {self.earliest:%d.%m.%Y}–{self.latest:%d.%m.%Y}"
        return f"{self.total_rows} saadetud uudiskirja{span}."


def _options(active: str, search: str) -> tuple[HistoryOption, ...]:
    options = [
        HistoryOption(
            key=ALL_NEWSLETTERS,
            label="Kõik",
            is_active=active == ALL_NEWSLETTERS,
            search=search,
        )
    ]
    for spec in NEWSLETTERS:
        registry_spec = spec_for(spec.metric)
        options.append(
            HistoryOption(
                key=spec.metric,
                label=registry_spec.label if registry_spec else spec.metric,
                is_active=active == spec.metric,
                search=search,
            )
        )
    if has_unclassified_campaigns():
        options.append(
            HistoryOption(
                key=OTHER_KEY,
                label=OTHER_LABEL,
                is_active=active == OTHER_KEY,
                search=search,
            )
        )
    return tuple(options)


def build_campaign_history(
    *,
    newsletter_key: str | None = None,
    search: str | None = None,
    page: str | int | None = None,
) -> CampaignHistory:
    """One page of the archive, narrowed by type and subject."""
    active = parse_newsletter(newsletter_key)
    term = parse_search(search)
    metric = None if active == ALL_NEWSLETTERS else active

    queryset = campaign_queryset(metric=metric, search=term)
    paginator = Paginator(queryset, PER_PAGE)

    # Over the whole filtered set, not the page. The summary states a count and
    # a span in one sentence, and taking the span from the fifty rows on screen
    # made "3 194 sends" read as though they all happened in three months.
    span = queryset.aggregate(earliest=Min("completed_at"), latest=Max("completed_at"))

    try:
        number = int(page) if page is not None else 1
    except TypeError, ValueError:
        # A hand-typed page is a bookmark that has rotted, not an error page.
        number = 1
    number = max(min(number, paginator.num_pages), 1)
    current = paginator.get_page(number)

    return CampaignHistory(
        rows=describe_campaigns(current.object_list),
        options=_options(active, term),
        search=term,
        active=active,
        page_number=current.number,
        total_pages=paginator.num_pages,
        total_rows=paginator.count,
        has_previous=current.has_previous(),
        has_next=current.has_next(),
        earliest=span["earliest"].date() if span["earliest"] else None,
        latest=span["latest"].date() if span["latest"] else None,
    )


__all__ = [
    "ALL_NEWSLETTERS",
    "PARAM_NEWSLETTER",
    "PARAM_PAGE",
    "PARAM_SEARCH",
    "PER_PAGE",
    "CampaignHistory",
    "HistoryOption",
    "build_campaign_history",
    "parse_newsletter",
    "parse_search",
]
