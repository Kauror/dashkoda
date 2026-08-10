"""Which Smaily segments make up which newsletter.

The account holds sixty segments and only five of them are durable lists. The
rest are one-off send audiences named after the day they were built —
`09.06.26 emta`, `24.04.26 margus`, `20.07.26 jäätmeseaduse muudatused` — and a
collector that mapped "every segment" onto the dashboard would produce a new
metric every time somebody sent a letter. So the mapping is an explicit
registry, and a segment that is not named here is stored but not shown.

## What the account actually contains

Read from the live account on 2026-08-10 through `GET /api/list.php`, not
assumed:

| id   | name                                            | subscribers |
|------|-------------------------------------------------|-------------|
| 2690 | `E-teataja list`                                | 8 008       |
| 2691 | `E-teataja list mitteliikmed`                   | 12 608      |
| 2711 | `E-News list`                                   | 755         |
| 2692 | `E-vestnik list - liikmed ja mitteliikmed koos`  | 527         |

## Why e-Teataja is two segments added together

Every other newsletter is one list. e-Teataja is two, and they are added.

That is a real decision and it is only defensible because the two are disjoint
by construction: one is the members' list and the other is explicitly
`mitteliikmed` — non-members. The campaign history confirms the Chamber treats
them that way, because each issue goes out as **two campaigns**, one per list:
`e-Teataja 30.07.26 liikmed` and `e-Teataja 4.08 mitteliikmed` are the same
issue sent twice. An address on both lists would receive the issue twice, which
is what the Chamber's own send practice is arranged to avoid.

The two counts are nevertheless **stored separately** and shown separately
beside the total. If the assumption above is ever wrong, no history has been
lost and the presentation is what changes — which is not true of a collector
that adds two numbers and stores the sum.

Note that the three *newsletters* are still never added to each other. A reader
subscribed to both e-Teataja and eNews is one person and two subscriptions, and
a combined "newsletter audience" figure would count them twice. Adding the two
halves of one list is a different operation from adding two lists.

## Why a segment can be withheld

The registry pins a segment by **id**, because a name is editable in Smaily's
interface and an id is not. But an id alone cannot notice that segment 2690 was
deleted and its number reused for something else, so each entry also carries a
token its name must still contain. A segment whose name has drifted is
**withheld** — the metric is published as absent with a reason, the other
newsletters publish normally, and nothing invents a figure for it.

That is the rule the rest of the dashboard already follows: withhold the
affected metric, never the whole observation, and never substitute zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .models import VisibilityMetric
from .smaily import SegmentReading, SegmentRow

#: Segment audiences, for the split shown beside a combined total.
AUDIENCE_MEMBERS = "Liikmed"
AUDIENCE_NON_MEMBERS = "Mitteliikmed"
AUDIENCE_ALL = ""


@dataclass(frozen=True)
class NewsletterSegment:
    """One Smaily segment that feeds one newsletter metric."""

    segment_id: int
    #: The name observed during the 2026-08-10 audit. Documentation, and what a
    #: mismatch message quotes so an operator can see what changed.
    expected_name: str
    #: Lowercase substring the name must still contain for the segment to be
    #: trusted. Deliberately short and stable: `e-teataja` survives a tidy-up
    #: that rewrites `E-teataja list` as `E-Teataja (liikmed)`, and still
    #: refuses a segment that has become something else entirely.
    name_token: str
    audience: str = AUDIENCE_ALL

    def matches(self, row: SegmentRow) -> bool:
        return self.name_token in row.name.casefold()


@dataclass(frozen=True)
class NewsletterSpec:
    """One newsletter and the segments that make up its audience."""

    metric: str
    segments: tuple[NewsletterSegment, ...]

    @property
    def is_split(self) -> bool:
        """Whether this newsletter's audience is more than one list."""
        return len(self.segments) > 1


NEWSLETTERS: tuple[NewsletterSpec, ...] = (
    NewsletterSpec(
        metric=VisibilityMetric.NEWSLETTER_ETEATAJA,
        segments=(
            NewsletterSegment(
                segment_id=2690,
                expected_name="E-teataja list",
                name_token="e-teataja",
                audience=AUDIENCE_MEMBERS,
            ),
            NewsletterSegment(
                segment_id=2691,
                expected_name="E-teataja list mitteliikmed",
                name_token="e-teataja",
                audience=AUDIENCE_NON_MEMBERS,
            ),
        ),
    ),
    NewsletterSpec(
        metric=VisibilityMetric.NEWSLETTER_ENEWS,
        segments=(
            NewsletterSegment(
                segment_id=2711,
                expected_name="E-News list",
                name_token="e-news",
            ),
        ),
    ),
    NewsletterSpec(
        metric=VisibilityMetric.NEWSLETTER_EVESTNIK,
        segments=(
            NewsletterSegment(
                segment_id=2692,
                expected_name="E-vestnik list - liikmed ja mitteliikmed koos",
                name_token="e-vestnik",
            ),
        ),
    ),
)

NEWSLETTERS_BY_METRIC: MappingProxyType[str, NewsletterSpec] = MappingProxyType(
    {spec.metric: spec for spec in NEWSLETTERS}
)

#: Every segment ID the dashboard maps, for a quick membership test.
MAPPED_SEGMENT_IDS: frozenset[int] = frozenset(
    segment.segment_id for spec in NEWSLETTERS for segment in spec.segments
)


@dataclass(frozen=True)
class SegmentPart:
    """One segment's contribution to a newsletter, as resolved against a reading."""

    segment: NewsletterSegment
    subscribers: int | None
    #: Empty when the part is usable; otherwise why it is not.
    withheld_reason: str = ""

    @property
    def is_available(self) -> bool:
        return self.subscribers is not None and not self.withheld_reason

    @property
    def label(self) -> str:
        return self.segment.audience or self.segment.expected_name


@dataclass(frozen=True)
class NewsletterAudience:
    """What one newsletter's audience is, according to one reading.

    `total` is `None` rather than `0` whenever any part is missing. A newsletter
    whose non-members list could not be read has an unknown audience, not a
    smaller one, and the difference has to survive all the way to the page.
    """

    spec: NewsletterSpec
    parts: tuple[SegmentPart, ...]

    @property
    def metric(self) -> str:
        return self.spec.metric

    @property
    def is_available(self) -> bool:
        return bool(self.parts) and all(part.is_available for part in self.parts)

    @property
    def total(self) -> int | None:
        if not self.is_available:
            return None
        return sum(part.subscribers or 0 for part in self.parts)

    @property
    def withheld_reason(self) -> str:
        """The first reason this newsletter has no figure, for the audit trail."""
        for part in self.parts:
            if part.withheld_reason:
                return part.withheld_reason
        return ""

    @property
    def visible_parts(self) -> tuple[SegmentPart, ...]:
        """The parts worth showing beside a total: only a genuine split."""
        return self.parts if self.spec.is_split else ()


def resolve_audience(spec: NewsletterSpec, reading: SegmentReading) -> NewsletterAudience:
    """Match one newsletter's segments against what Smaily reported."""
    rows = reading.by_id()
    parts = []
    for segment in spec.segments:
        row = rows.get(segment.segment_id)
        if row is None:
            parts.append(
                SegmentPart(
                    segment=segment,
                    subscribers=None,
                    withheld_reason=(
                        f"Smaily ei tagastanud segmenti {segment.segment_id} "
                        f"({segment.expected_name})."
                    ),
                )
            )
            continue
        if not segment.matches(row):
            # The id resolved to a list that is no longer the one described
            # here. Publishing its number would move another audience's figure
            # onto this newsletter's chart, and nothing afterwards would look
            # wrong.
            parts.append(
                SegmentPart(
                    segment=segment,
                    subscribers=None,
                    withheld_reason=(
                        f"Segmendi {segment.segment_id} nimi ei vasta enam ootusele "
                        f"„{segment.expected_name}“. Kontrolli Smaily seadistust."
                    ),
                )
            )
            continue
        parts.append(SegmentPart(segment=segment, subscribers=row.subscribers))
    return NewsletterAudience(spec=spec, parts=tuple(parts))


def resolve_all(reading: SegmentReading) -> tuple[NewsletterAudience, ...]:
    """Every newsletter's audience from one reading, in registry order."""
    return tuple(resolve_audience(spec, reading) for spec in NEWSLETTERS)


def _check_registry() -> None:
    """Refuse to import a registry that has drifted.

    Three things can rot here — a metric that is not a real newsletter metric, a
    segment mapped to two newsletters, and a token that does not match the name
    it was written from. All three become an immediate error rather than a wrong
    page.
    """
    newsletter_metrics = {
        VisibilityMetric.NEWSLETTER_ETEATAJA,
        VisibilityMetric.NEWSLETTER_ENEWS,
        VisibilityMetric.NEWSLETTER_EVESTNIK,
    }
    described = {spec.metric for spec in NEWSLETTERS}
    if described != newsletter_metrics:
        missing = sorted(newsletter_metrics - described)
        extra = sorted(described - newsletter_metrics)
        raise RuntimeError(
            f"Smaily newsletter registry disagrees with VisibilityMetric: "
            f"missing={missing} extra={extra}"
        )
    if len(NEWSLETTERS_BY_METRIC) != len(NEWSLETTERS):
        raise RuntimeError("Smaily newsletter registry contains a duplicate metric.")

    seen: set[int] = set()
    for spec in NEWSLETTERS:
        for segment in spec.segments:
            if segment.segment_id in seen:
                raise RuntimeError(
                    f"Smaily segment {segment.segment_id} is mapped to more than one newsletter."
                )
            seen.add(segment.segment_id)
            if segment.name_token != segment.name_token.casefold():
                raise RuntimeError(f"Smaily segment {segment.segment_id} token must be lowercase.")
            if segment.name_token not in segment.expected_name.casefold():
                raise RuntimeError(
                    f"Smaily segment {segment.segment_id} token does not match its own "
                    f"expected name."
                )


_check_registry()


__all__ = [
    "AUDIENCE_ALL",
    "AUDIENCE_MEMBERS",
    "AUDIENCE_NON_MEMBERS",
    "MAPPED_SEGMENT_IDS",
    "NEWSLETTERS",
    "NEWSLETTERS_BY_METRIC",
    "NewsletterAudience",
    "NewsletterSegment",
    "NewsletterSpec",
    "SegmentPart",
    "resolve_all",
    "resolve_audience",
]
