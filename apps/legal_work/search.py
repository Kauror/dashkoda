"""Looking a legal-work record up, rather than reading the two standing lists.

The Õigusloome page answers two standing questions — what is in work, and what
went out most recently — and answers them well. It could not answer a third:
"what did we say about X". `Hetkel töös` is the open records and `Viimati välja
läinud` is the fifteen newest sends, which together drew 33 of the 612 records
in the snapshot. The other 579 were stored, imported, matched to their public
consultations, and unreachable from the page that is about them.

So this is a search over the **whole current snapshot**, and the design follows
from that one word:

- **current.** A retired snapshot is provenance, not an answer. Searching one
  would surface a topic whose stage has since changed and present it as today's
  position;
- **whole.** Open and concluded alike. A concluded record is exactly the thing
  somebody looks up, because they already know the open ones;
- **snapshot.** Not the Koda.ee public archive, which is a different register
  with 5 699 rows of its own. The two are never merged into one result list:
  they count different things and one is the Chamber's internal register.

The search is a mode, not a replacement. With no term the page is exactly what
it was, and the two standing lists stay on screen either way — the reader asked
an extra question, not a different one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import quote

from django.core.paginator import EmptyPage, Paginator

from .selectors import (
    MAX_SEARCH_LENGTH,
    SEARCH_ALL,
    SEARCH_OPEN,
    SEARCH_SENT,
    SEARCH_STATUSES,
    search_items,
)

#: The query parameters this section reads.
PARAM_QUERY = "otsing"
PARAM_STATUS = "seis"
PARAM_PAGE = "lk"

#: Rows per page. The register is six hundred records, so a search that matched
#: broadly still has to paginate; twenty-five is a screenful of a compact table.
PER_PAGE = 25

#: The status chips, in the order they are read.
STATUS_LABELS: tuple[tuple[str, str], ...] = (
    (SEARCH_ALL, "Kõik"),
    (SEARCH_OPEN, "Töös"),
    (SEARCH_SENT, "Välja läinud"),
)


def parse_query(raw: str | None) -> str:
    """The term, trimmed and bounded. Never raises, never reaches SQL as SQL."""
    return (raw or "").strip()[:MAX_SEARCH_LENGTH]


def parse_status(raw: str | None) -> str:
    """The status asked for, or all of them."""
    value = (raw or "").strip()
    return value if value in SEARCH_STATUSES else SEARCH_ALL


def parse_page(raw: str | int | None) -> int:
    """The page asked for, floored at one. A rotted bookmark is not an error."""
    try:
        return max(int(raw), 1)
    except TypeError, ValueError:
        return 1


def build_query(*, query: str, status: str, page: int | None = None) -> str:
    """One URL's worth of state, from validated values only.

    Every control links through here, which is what makes them compose: changing
    the status keeps the term, and paging keeps both. Copying `request.GET` and
    editing one key would carry whatever else was in the URL, including a page
    number that no longer exists.
    """
    parts = [f"{PARAM_QUERY}={quote(query)}"]
    if status:
        parts.append(f"{PARAM_STATUS}={quote(status)}")
    if page and page > 1:
        parts.append(f"{PARAM_PAGE}={page}")
    return "&".join(parts)


@dataclass(frozen=True)
class StatusOption:
    key: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class LegalWorkSearch:
    """Everything the search section renders."""

    query: str
    status: str
    statuses: tuple[StatusOption, ...]
    #: `LegalWorkItem` rows as built, and `LegalTopicPresentation` rows once
    #: the view has called `presented_with`. Nothing renders the first shape:
    #: the template only ever sees a presented section.
    results: tuple = ()
    total: int = 0
    page_number: int = 1
    total_pages: int = 1
    #: How many records the search looked at, so "3 of 612" is sayable.
    population: int = 0

    @property
    def is_searching(self) -> bool:
        return bool(self.query)

    @property
    def has_results(self) -> bool:
        return bool(self.results)

    @property
    def has_previous(self) -> bool:
        return self.page_number > 1

    @property
    def has_next(self) -> bool:
        return self.page_number < self.total_pages

    @property
    def summary(self) -> str:
        """What was found, in words, so a bare count never stands alone."""
        if not self.is_searching:
            return ""
        if not self.total:
            return "Ühtegi kirjet ei leitud."
        if self.total == 1:
            return "1 kirje."
        return f"{self.total} kirjet."

    @property
    def scope_note(self) -> str:
        """What was searched. The point of the feature is that it is everything,
        and a reader cannot tell that from a result list."""
        if not self.population:
            return ""
        return f"Otsitakse kõigist registri kirjetest ({self.population})."

    @property
    def clear_query(self) -> str:
        """Back to the page without a search, keeping nothing: clearing a search
        is finishing with it."""
        return ""

    def presented_with(self, links: dict[int, str]) -> LegalWorkSearch:
        """The same section with its rows turned into linkable presentations.

        Deliberately a second step rather than something `build_search` does.
        The view resolves public links for every list on the page in **one**
        query, and a record that appears both in `Hetkel töös` and in a search
        result must be asked about once and answered identically — which is only
        true if the search joins that call instead of making its own.
        """
        from .topic_links import present_topics

        return replace(self, results=present_topics(self.results, links))

    def page_query(self, page: int) -> str:
        return build_query(query=self.query, status=self.status, page=page)

    @property
    def previous_query(self) -> str:
        return self.page_query(max(self.page_number - 1, 1))

    @property
    def next_query(self) -> str:
        return self.page_query(min(self.page_number + 1, max(self.total_pages, 1)))


def _status_options(active: str, query: str) -> tuple[StatusOption, ...]:
    return tuple(
        StatusOption(
            key=key,
            label=label,
            is_active=key == active,
            query=build_query(query=query, status=key),
        )
        for key, label in STATUS_LABELS
    )


def build_search(
    snapshot,
    *,
    query: str = "",
    status: str = SEARCH_ALL,
    page: int = 1,
    population: int = 0,
) -> LegalWorkSearch:
    """Resolve a search over the whole snapshot, or an empty section.

    Returns the section either way: with no term it is just the box and the
    chips, which is what keeps the page unchanged for a reader who never
    searches. The rows come back **unpresented**; `presented_with` is the second
    step, so the search joins the view's single link query rather than adding a
    second one.
    """
    section = LegalWorkSearch(
        query=query,
        status=status,
        statuses=_status_options(status, query),
        population=population,
    )
    if not query:
        return section

    paginator = Paginator(search_items(snapshot, query=query, status=status), PER_PAGE)
    try:
        current = paginator.page(page)
    except EmptyPage:
        # Past the end is a stale bookmark; the last page is the closest true
        # answer to what it asked for.
        current = paginator.page(paginator.num_pages)

    return replace(
        section,
        results=tuple(current.object_list),
        total=paginator.count,
        page_number=current.number,
        total_pages=paginator.num_pages,
    )


__all__ = [
    "PARAM_PAGE",
    "PARAM_QUERY",
    "PARAM_STATUS",
    "PER_PAGE",
    "LegalWorkSearch",
    "StatusOption",
    "build_query",
    "build_search",
    "parse_page",
    "parse_query",
    "parse_status",
]
