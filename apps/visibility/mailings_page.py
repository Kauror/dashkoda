"""What the Otsepostitused page says.

`Otsepostitused` is the Chamber's newsletter intelligence: how each list
performs, how the recent sends compare with the block before them, which sends
did best, and — one route along — every campaign ever sent.

## Where this came from

The material was the fifth focus of `/uudised/`, and its composition lived in
`apps/news/page.py` while the presenters and every Smaily query lived here. It
is one section now, at `/otsepostitused/` under Koduleht, and the composition
has come to sit beside the data it composes. **Nothing was reimplemented.**
`smaily_selectors` answers exactly the questions it answered before, with the
same aggregates over the same windows, and `newsletter_page.py` still builds
the searchable sends section it always did.

So the arithmetic in this module is the arithmetic that was in `news.page`,
moved: same block size, same weighted rates, same five-row rankings. If a
figure here disagrees with what Uudised used to show, that is a defect and not
a redefinition.

## Two things this must never do

- **average percentages.** A rate is summed opens over summed delivered. Taking
  the mean of per-issue percentages would weight a send to 755 people the same
  as one to 20 616, and the headline would drift towards the smallest list;
- **total the newsletters.** Three lists, three audiences, and a reader on two
  of them is one person. Nothing here adds them up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.core.change import ChangeRow, direction_of, share_percent
from apps.core.formatting import integer, percentage_points, signed_integer

from . import smaily_charts
from .smaily_segments import NEWSLETTERS
from .smaily_selectors import (
    DEFAULT_AGGREGATE_ISSUES,
    get_campaign_performance,
    get_newsletter_aggregate,
)

#: How many recent sends the rate history draws.
SENDS_LIMIT = 24

#: How many sends a ranking lists.
RANKING_LIMIT = 5

#: How deep the ranking looks before sorting. A bound rather than the whole
#: history: fourteen years of e-Teataja is thousands of rows, and the strongest
#: five sends of the last two hundred is the question anybody is actually
#: asking.
RANKING_DEPTH = 200


@dataclass(frozen=True)
class MailingsPage:
    """Everything the Otsepostitused overview renders.

    `selected_newsletter` empty means no newsletter is chosen: the page shows
    the comparison across all three and nothing that would need one of them
    picked. That is the landing state, not an error state.
    """

    #: One line per newsletter, never summed.
    comparison: tuple = field(default_factory=tuple)
    recent: object | None = None
    previous: object | None = None
    changes: tuple[ChangeRow, ...] = field(default_factory=tuple)
    sends: object | None = None
    rankings: dict = field(default_factory=dict)
    selected_newsletter: str = ""

    @property
    def has_selection(self) -> bool:
        return bool(self.selected_newsletter)

    @property
    def has_rankings(self) -> bool:
        return bool(self.rankings.get("by_open") or self.rankings.get("by_click"))


def build_mailings_page(
    *, newsletter_key: str = "", sends_limit: int = SENDS_LIMIT
) -> MailingsPage:
    """The overview, reading only what the state on screen actually renders.

    With no newsletter chosen this is three aggregate queries and nothing else:
    the rate history, the block comparison and the rankings all describe one
    newsletter, and running them for a page that shows none would be three
    views' worth of queries behind a table of three rows.
    """
    comparison = tuple(_summary(spec.metric) for spec in NEWSLETTERS)

    if not newsletter_key:
        return MailingsPage(comparison=comparison)

    # One block size for both slices, so "the twelve before" is always the same
    # twelve the recent figure is quoted over.
    recent = get_newsletter_aggregate(newsletter_key, limit=DEFAULT_AGGREGATE_ISSUES)
    previous = get_newsletter_aggregate(
        newsletter_key, limit=DEFAULT_AGGREGATE_ISSUES, offset=DEFAULT_AGGREGATE_ISSUES
    )
    sends = [
        send
        for send in get_campaign_performance(metric=newsletter_key, limit=sends_limit)
        if send.has_statistics
    ]
    sends.reverse()

    return MailingsPage(
        comparison=comparison,
        recent=recent,
        previous=previous,
        changes=_changes(recent, previous),
        sends=smaily_charts.newsletter_rates(sends) if sends else None,
        rankings=_rankings(newsletter_key),
        selected_newsletter=newsletter_key,
    )


def _summary(metric: str) -> dict:
    """One newsletter's own line in the comparison. Never added to another's.

    The label comes from the visibility registry through the aggregate, which is
    the one place a newsletter is named; `smaily_segments` maps segments to
    newsletters and does not carry display text.

    Carries only the two rates. Send and delivery counts left this comparison
    on 2026-08-17 — they duplicated the newsletter card above rather than
    telling the reader anything the rates didn't.
    """
    aggregate = get_newsletter_aggregate(metric)
    return {
        "metric": metric,
        "label": aggregate.label,
        "open_rate": share_percent(aggregate.open_rate),
        "click_rate": share_percent(aggregate.click_rate),
    }


def _changes(recent, previous) -> tuple[ChangeRow, ...]:
    """Recent sends against the block before them, on weighted rates only.

    Never the mean of per-send percentages: a send to 755 people and one to
    20 616 are not two equally weighted observations, and averaging the
    percentages would drag the headline towards whichever list is smallest.
    Summed counts over summed counts is the only aggregate quoted.
    """
    if not recent.has_data or not previous.has_data:
        return ()
    rows = []
    for label, current_value, earlier_value in (
        ("Avamismäär", recent.open_rate, previous.open_rate),
        ("Klikimäär", recent.click_rate, previous.click_rate),
        ("Klikke avajate seas", recent.click_to_open_rate, previous.click_to_open_rate),
    ):
        if current_value is None or earlier_value is None:
            continue
        difference = (current_value - earlier_value) * 100
        rows.append(
            ChangeRow(
                label=label,
                current=share_percent(current_value),
                previous=share_percent(earlier_value),
                change=percentage_points(difference),
                direction=direction_of(difference),
            )
        )
    if recent.delivered and previous.delivered:
        difference = recent.delivered - previous.delivered
        rows.append(
            ChangeRow(
                label="Kättetoimetatud",
                current=integer(recent.delivered),
                previous=integer(previous.delivered),
                change=signed_integer(difference),
                direction=direction_of(difference),
            )
        )
    return tuple(rows)


def _rankings(metric: str, *, limit: int = RANKING_LIMIT) -> dict:
    """The strongest sends by rate, with the audience size beside each.

    A rate without its denominator misleads: 62% of 30 recipients and 41% of
    20 000 are not the same achievement, and only one of them is a newsletter.
    Sends from other newsletters are not in here — `Muu` least of all, which is
    not a newsletter but every other kind of letter the Chamber has ever sent.
    """
    measured = [
        send
        for send in get_campaign_performance(metric=metric, limit=RANKING_DEPTH)
        if send.has_statistics and send.delivered
    ]
    by_open = sorted(
        (send for send in measured if send.open_rate is not None),
        key=lambda send: send.open_rate,
        reverse=True,
    )[:limit]
    by_click = sorted(
        (send for send in measured if send.click_rate is not None),
        key=lambda send: send.click_rate,
        reverse=True,
    )[:limit]
    return {"by_open": tuple(by_open), "by_click": tuple(by_click)}


__all__ = [
    "RANKING_LIMIT",
    "SENDS_LIMIT",
    "MailingsPage",
    "build_mailings_page",
]
