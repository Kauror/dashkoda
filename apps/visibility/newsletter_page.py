"""The newsletter-analytics section of the Uudised page.

The card above it answers "how many people can the Chamber reach". This answers
the one question a card cannot: does anybody read what is sent.

It lived under Nähtavus until the newsletter material moved to Uudised, where a
reader already is when they are asking what the Chamber published. The module
stays here because the data is Smaily's and Smaily belongs to `apps.visibility`;
only where it is rendered changed.

Two things it must never do, and each has a specific way of going wrong:

- **average percentages.** A newsletter's open rate here is summed opens over
  summed delivered. Taking the mean of per-issue percentages would weight a send
  to 755 people the same as one to 20 616, and the headline figure would drift
  towards whichever list is smallest;
- **mix the newsletters.** Three separate lists, three separate audiences, and
  a reader on two of them is one person. Nothing here totals across them.

**How large each list is was removed from this section on 2026-08-11**, first as
three sparklines and then as the rows underneath them. The band above already
prints all three under `Uudiskirjad`, so this section was a second copy of the
same figures, and the charts on top of them were two readings a day apart drawn
as a trend. Nothing was lost: `get_all_subscriber_series` still answers, and
`build_newsletter_slot` in `page.py` still shows the counts.

What went with them is `coverage_note` — the sentence about Smaily holding a
list's present size and not its history. It was already computed-but-unprinted,
and with the counts gone it described something no longer on the page.
`docs/newsletter-audience.md` carries the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from apps.core.formatting import group_thousands, percent

from .registry import spec_for
from .smaily_campaigns import AUDIENCE_MEMBERS, AUDIENCE_NON_MEMBERS, OTHER_KEY, OTHER_LABEL
from .smaily_segments import NEWSLETTERS
from .smaily_selectors import (
    MAX_SEARCH_LENGTH,
    CampaignPerformance,
    NewsletterAggregate,
    campaign_queryset,
    get_campaign_performance,
    get_newsletter_aggregate,
    has_unclassified_campaigns,
)

#: The query parameter the newsletter filter carries.
PARAM_NEWSLETTER = "uudiskiri"

#: The subject search, under the same name the archive page uses. One term means
#: one thing across both pages, and carrying it from here to the archive is then
#: a matter of copying the value rather than translating it.
PARAM_SEARCH = "otsi"

#: How many issues the performance table lists. Long enough to show a pattern,
#: short enough that nobody scrolls past the point of interest.
TOP_ISSUES = 15

#: How many recent issues an aggregate rate is computed over. About a quarter of
#: e-Teataja's cadence, which is recent enough to describe how the newsletter
#: performs now rather than how it performed two years ago.
AGGREGATE_ISSUES = 12

#: The filter value meaning "all three".
ALL_NEWSLETTERS = "koik"

_AUDIENCE_LABELS = {
    AUDIENCE_MEMBERS: "Liikmed",
    AUDIENCE_NON_MEMBERS: "Mitteliikmed",
}


def parse_newsletter(raw: str | None) -> str:
    """The newsletter asked for, or all of them. Never raises.

    Validated against a closed set — the three newsletters plus `Muu` — so a
    hand-typed value falls back to "all" rather than reaching a query.
    """
    value = (raw or "").strip()
    if value == OTHER_KEY or any(spec.metric == value for spec in NEWSLETTERS):
        return value
    return ALL_NEWSLETTERS


def parse_search(raw: str | None) -> str:
    """The subject search, trimmed and bounded. Never reaches SQL as SQL.

    Free text, unlike the newsletter key, which is validated against a closed
    set. What bounds it instead is the length cap and the ORM: the term is only
    ever a parameter to `icontains`.
    """
    return (raw or "").strip()[:MAX_SEARCH_LENGTH]


def _query(newsletter: str, search: str = "", carried: str = "") -> str:
    query = f"{PARAM_NEWSLETTER}={newsletter}"
    if search:
        query += f"&{PARAM_SEARCH}={quote(search)}"
    # The other section's state first, so the reader's news archive survives a
    # newsletter click. It arrives already built and already validated by
    # `apps.news.periods.build_query`; nothing here parses a raw query string or
    # copies one through, because this value ends up in somebody's address bar.
    return f"{carried}&{query}" if carried else query


def newsletter_state(*, newsletter_key: str | None = None, search: str | None = None) -> str:
    """This section's own state as a query fragment, validated and nothing else.

    What the news archive on the same page carries through its links so that
    choosing a period cannot clear the reader's newsletter. Empty when the
    section is at its defaults, so an untouched page keeps an untouched URL.

    The values are parsed by the same two functions the section itself uses, so
    what is carried is what was applied — never the raw parameter.
    """
    active = parse_newsletter(newsletter_key)
    term = parse_search(search)
    if active == ALL_NEWSLETTERS and not term:
        return ""
    return _query(active, term)


@dataclass(frozen=True)
class NewsletterOption:
    """One filter button: what it says, where it goes, whether it is active."""

    key: str
    label: str
    is_active: bool
    #: Whatever search is in force, so switching newsletter keeps the question.
    search: str = ""
    #: The news archive's own state, carried through untouched. Since this
    #: section moved onto `/uudised/`, a chip that dropped it would reset the
    #: reader's period, category, ordering and news search on every click.
    carried: str = ""

    @property
    def query(self) -> str:
        return _query(self.key, self.search, self.carried)


def newsletter_options(
    active: str, *, with_other: bool = False, search: str = "", carried: str = ""
) -> tuple[NewsletterOption, ...]:
    """`Kõik`, the three newsletters, and `Muu` when it leads somewhere.

    `Muu` is offered only when unclassified sends actually exist. A filter that
    always returns nothing teaches a reader that the section is broken; on this
    account it is the largest group there is, so it is nearly always shown.

    Every option carries the current search. Dropping it would mean a reader who
    has found "aastakoosolek" in e-Teataja and clicks `Kõik` silently gets the
    fifteen most recent sends instead of the same search widened, which is not
    what the button says it does.
    """
    options = [
        NewsletterOption(
            key=ALL_NEWSLETTERS,
            label="Kõik",
            is_active=active == ALL_NEWSLETTERS,
            search=search,
            carried=carried,
        )
    ]
    for spec in NEWSLETTERS:
        registry_spec = spec_for(spec.metric)
        options.append(
            NewsletterOption(
                key=spec.metric,
                label=registry_spec.label if registry_spec else spec.metric,
                is_active=active == spec.metric,
                search=search,
                carried=carried,
            )
        )
    if with_other:
        options.append(
            NewsletterOption(
                key=OTHER_KEY,
                label=OTHER_LABEL,
                is_active=active == OTHER_KEY,
                search=search,
                carried=carried,
            )
        )
    return tuple(options)


@dataclass(frozen=True)
class PerformanceFigure:
    """One aggregate rate, already spelled, with its denominator named."""

    label: str
    value: str
    note: str = ""

    @property
    def has_value(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class NewsletterSection:
    """Everything the newsletter-analytics section renders."""

    active: str
    options: tuple[NewsletterOption, ...]
    figures: tuple[PerformanceFigure, ...]
    issues: tuple[CampaignPerformance, ...]
    aggregate: NewsletterAggregate | None
    #: The subject search in force, already trimmed and bounded.
    search: str = ""
    #: How many completed sends match the current newsletter *and* search — the
    #: population the fifteen rows are the newest of. It was once the unfiltered
    #: total, which read as "3 194 more where these came from" beside a table
    #: showing e-Teataja alone.
    total_sends: int = 0
    #: The news archive's state, carried through every link this section builds
    #: that stays on `/uudised/`. Empty when the section is rendered anywhere
    #: that has no news archive to preserve.
    carried: str = ""

    @property
    def is_searching(self) -> bool:
        return bool(self.search)

    @property
    def has_more_sends(self) -> bool:
        return self.total_sends > len(self.issues)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    @property
    def has_any_data(self) -> bool:
        """Whether this section has anything to show at all.

        Sends, and no longer subscriber readings. While the audience rows were
        here a Smaily-connected account with lists but no campaigns rendered a
        populated section; now it has nothing to say and says so.

        `or self.is_searching` for the reason the traffic section guards its
        ranking the same way: a search matching nothing must not take the whole
        section off the page, because the box that would clear it goes with it.
        """
        return self.has_issues or self.is_searching

    @property
    def is_filtered(self) -> bool:
        return self.active != ALL_NEWSLETTERS

    @property
    def result_summary(self) -> str:
        """What the search found, in words, so a count is never bare."""
        if not self.total_sends:
            return "Ühtegi saadetud uudiskirja ei leitud."
        if self.total_sends == 1:
            return "1 saadetud uudiskiri."
        return f"{self.total_sends} saadetud uudiskirja."

    @property
    def clear_query(self) -> str:
        """Back to the recent sends, keeping the newsletter.

        Clearing a search is not starting again: the reader still wants
        e-Teataja, they have simply finished looking for one issue — and they
        are still looking at whatever news archive they had narrowed to, which
        is why this carries it and `archive_query` below does not.
        """
        return _query(self.active, carried=self.carried)

    @property
    def archive_query(self) -> str:
        """The archive, asking the question this section is asking.

        Both pages read `uudiskiri` and `otsi`, so "see all" opens the same
        newsletter and the same term rather than fourteen unfiltered years.

        Deliberately without `carried`: this link leaves the news archive for a
        page that has none, and `periood` or `kategooria` in that URL would be
        parameters the archive page does not understand.
        """
        return _query(self.active, self.search)


def build_newsletter_section(
    *, newsletter_key: str | None = None, search: str | None = None, carried: str = ""
) -> NewsletterSection:
    """Read the stored campaign history once and shape it for the page.

    Three queries and no subscriber reading: this section is about sends now, so
    the three `get_all_subscriber_series` calls it used to make on every render
    are gone with the rows they fed.

    `carried` is the news archive's own query string, already built and already
    validated by the caller. It is threaded onto the links that stay on
    `/uudised/` so this section cannot reset the archive above it.
    """
    active = parse_newsletter(newsletter_key)
    term = parse_search(search)
    metric = None if active == ALL_NEWSLETTERS else active

    issues = get_campaign_performance(metric=metric, limit=TOP_ISSUES, search=term)
    aggregate = (
        get_newsletter_aggregate(metric, limit=AGGREGATE_ISSUES)
        if metric is not None and metric != OTHER_KEY
        else None
    )

    return NewsletterSection(
        active=active,
        options=newsletter_options(
            active, with_other=has_unclassified_campaigns(), search=term, carried=carried
        ),
        figures=_figures(aggregate) if aggregate is not None else (),
        issues=issues,
        aggregate=aggregate,
        search=term,
        total_sends=campaign_queryset(metric=metric, search=term).count(),
        carried=carried,
    )


def _figures(aggregate: NewsletterAggregate) -> tuple[PerformanceFigure, ...]:
    """The rates above the table, each naming what it divided by."""
    if not aggregate.has_data:
        return ()

    figures = [
        PerformanceFigure(
            label="Kohale toimetatud",
            value=group_thousands(aggregate.delivered or 0),
            note=f"Kokku {aggregate.campaigns} viimase numbri peale.",
        )
    ]
    if aggregate.open_rate is not None:
        figures.append(
            PerformanceFigure(
                label="Avamismäär",
                value=percent(100 * aggregate.open_rate),
                # Named, because Smaily's own percentage means this and a reader
                # comparing it with another tool's figure needs to know.
                note="Avamised kohale toimetatud kirjadest.",
            )
        )
    if aggregate.click_rate is not None:
        figures.append(
            PerformanceFigure(
                label="Klikimäär",
                value=percent(100 * aggregate.click_rate),
                note="Unikaalsed klikid kohale toimetatud kirjadest.",
            )
        )
    if aggregate.click_to_open_rate is not None:
        figures.append(
            PerformanceFigure(
                label="Klikke avajate seas",
                value=percent(100 * aggregate.click_to_open_rate),
                # A different denominator from the one above it, so it says so.
                note="Unikaalsed klikid avamistest.",
            )
        )
    return tuple(figures)


def audience_label(audience: str) -> str:
    return _AUDIENCE_LABELS.get(audience, "")


__all__ = [
    "AGGREGATE_ISSUES",
    "ALL_NEWSLETTERS",
    "PARAM_NEWSLETTER",
    "PARAM_SEARCH",
    "TOP_ISSUES",
    "NewsletterOption",
    "NewsletterSection",
    "PerformanceFigure",
    "audience_label",
    "build_newsletter_section",
    "newsletter_options",
    "newsletter_state",
    "parse_newsletter",
    "parse_search",
]
