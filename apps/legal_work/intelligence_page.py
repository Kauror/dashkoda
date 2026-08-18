"""What the Õigusloome page draws, assembled once per request.

The page keeps one URL and one server render. A `fookus` parameter chooses which
analytical surface is built, so every view is bookmarkable, shareable and
reload-safe, and the browser back button moves between them the way it moves
between any two pages.

Only the focus a reader asked for is computed. The overview does not pay for the
response-window distribution, and the register does not pay for the annual
series — a focus that is not drawn issues no queries at all.

Nothing here parses a request parameter by hand. `fookus` is a closed set and an
unknown value falls back to the overview rather than raising or rendering an
empty page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import charts
from .analytics import (
    DataQuality,
    FeedbackSummary,
    StageBreakdown,
    annual_sent_opinions,
    annual_topics,
    count_sent_in_year,
    count_topics_for_year,
    data_quality,
    feedback_breakdown,
    feedback_coverage_by_year,
    feedback_summary,
    monthly_new_topics,
    monthly_sent_opinions,
    recipient_breakdown,
    response_window_by_year,
    response_window_distribution,
    sent_year_on_year,
    stage_breakdown,
    topics_year_on_year,
    warning_code_counts,
)
from .selectors import (
    get_upcoming_deadlines,
)

PARAM_FOCUS = "fookus"

FOCUS_OVERVIEW = "ulevaade"
FOCUS_WORKFLOW = "toovoog"

#: The closed set, in the order the navigation draws it. A value outside it is
#: not an error the reader caused on purpose — a truncated link, an old
#: bookmark — so it resolves to the overview instead of a 404.
#:
#: `arvamused`, `tagasiside` and `register` left the set on 2026-08-18, when
#: the five-focus page became two: their charts moved onto `toovoog` — now
#: labelled for both halves of what it draws — and the register explorer
#: moved onto the overview, unconditionally, next to the standing lists it
#: used to sit one click away from. A link still carrying any of the three
#: resolves to the overview, same as any other stale value; `aktiivsed`,
#: retired on 2026-08-17, already worked this way.
FOCUS_CHOICES: tuple[tuple[str, str], ...] = (
    (FOCUS_OVERVIEW, "Ülevaade"),
    (FOCUS_WORKFLOW, "Töövoog ja arvamused"),
)
FOCUS_KEYS = frozenset(key for key, _label in FOCUS_CHOICES)

#: How many approaching deadlines the overview lists before linking onward. The
#: front page answers "what has to leave next", not "everything with a date".
OVERVIEW_DEADLINE_LIMIT = 5

#: Below this many measured topics the feedback breakdowns are not drawn at all.
#: A ranking built from two measured matters describes the measurement rather
#: than the participation, and an empty chart beside a "measurement is still
#: starting" note contradicts the note.
MIN_FEEDBACK_TOPICS_FOR_BREAKDOWN = 10


def parse_focus(raw: str | None) -> str:
    """The requested focus, or the overview when it is not one we draw."""
    value = (raw or "").strip().lower()
    return value if value in FOCUS_KEYS else FOCUS_OVERVIEW


@dataclass(frozen=True)
class FocusLink:
    key: str
    label: str
    url: str
    is_active: bool


def focus_links(page_url: str, active: str) -> tuple[FocusLink, ...]:
    """The navigation, built from validated values rather than from the query.

    The overview is the bare page rather than `?fookus=ulevaade`, so the default
    view has one address instead of two that render identically.
    """
    return tuple(
        FocusLink(
            key=key,
            label=label,
            url=page_url if key == FOCUS_OVERVIEW else f"{page_url}?{PARAM_FOCUS}={key}",
            is_active=key == active,
        )
        for key, label in FOCUS_CHOICES
    )


@dataclass(frozen=True)
class Headline:
    """One hero figure, already formatted, with its comparison if it has one."""

    label: str
    value: str
    note: str = ""
    change: str = ""
    change_label: str = ""
    direction: str = ""

    @property
    def has_change(self) -> bool:
        return bool(self.change)


@dataclass(frozen=True)
class IntelligencePage:
    """Everything one render of `/oigusloome/` needs.

    A focus that was not requested leaves its fields empty, so a template can
    ask whether a section has content without knowing which focus produced it.
    """

    focus: str
    focus_label: str
    links: tuple[FocusLink, ...]
    reporting_year: int = 0
    headlines: tuple[Headline, ...] = ()
    #: The monthly pair — new topics and sent opinions — drawn side by side
    #: under one `Kuude lõikes` heading, kept apart from `charts` because
    #: nothing else on the page shares that two-up layout.
    monthly_charts: tuple = ()
    charts: tuple = ()
    deadlines: tuple = ()
    stage: StageBreakdown | None = None
    feedback: FeedbackSummary | None = None
    feedback_coverage: tuple = ()
    feedback_topics: tuple = ()
    feedback_start_year: int | None = None
    quality: DataQuality | None = None
    warning_codes: tuple = ()
    footnotes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_charts(self) -> bool:
        return bool(self.charts)


# --------------------------------------------------------------------------
# Headlines
# --------------------------------------------------------------------------


def _headlines(snapshot, year: int) -> tuple[Headline, ...]:
    """The four answers the overview leads with.

    `Teemasid {year}` deliberately carries no year-on-year delta on the figure
    itself — it is a stock counted by the register's own annual grouping,
    which keeps growing until the year closes, so it cannot be clamped to a
    comparable date the way a datable event can, and a delta on the stock
    would compare a part-year total with a finished one. Its *note* carries a
    real comparison instead: `topics_year_on_year` clamps both sides to the
    same day of the year, which is exactly what the stock delta cannot do.

    `Liikmed andsid tagasisidet` reads `FeedbackCoverageYear.with_feedback`,
    never `tracked_topics` — the two answer different questions. A topic is
    "tracked" the moment its feedback field is measured at all, including a
    measured zero; it "has feedback" only once a member actually answered.
    The card states participation, not measurement completeness.
    """
    from apps.core.formatting import integer, signed_integer, signed_percent

    topics = count_topics_for_year(snapshot, year)
    sent = count_sent_in_year(snapshot, year, cutoff=snapshot.reporting_date)
    active = stage_breakdown(snapshot).total

    arrivals = topics_year_on_year(snapshot)
    arrivals_note = ""
    if arrivals is not None:
        arrivals_note = (
            f"uusi sisse tulnud {integer(arrivals.current)} · "
            f"{signed_integer(arrivals.absolute_change)} vs sama aeg "
            f"{arrivals.previous_cutoff.year}"
        )

    sent_change = sent_year_on_year(snapshot)
    sent_note = ""
    sent_change_label = ""
    sent_direction = ""
    if sent_change is not None:
        sent_note = f"{integer(sent_change.current)} vs {integer(sent_change.previous)}"
        percent_text = (
            signed_percent(sent_change.percent_change)
            if sent_change.percent_change is not None
            else "–"
        )
        sent_note = f"{percent_text} · {sent_note}"
        sent_change_label = (
            f"{signed_integer(sent_change.absolute_change)} võrreldes eelmise aasta "
            f"sama kuupäevaga, {percent_text}"
        )
        sent_direction = sent_change.direction

    coverage_by_year = {entry.year: entry for entry in feedback_coverage_by_year(snapshot)}
    this_coverage = coverage_by_year.get(year)
    previous_coverage = coverage_by_year.get(year - 1)
    feedback_value = "–"
    feedback_note = ""
    if this_coverage is not None:
        feedback_value = (
            f"{integer(this_coverage.with_feedback)} / {integer(this_coverage.total_topics)} teemal"
        )
        share = (
            this_coverage.with_feedback / this_coverage.total_topics * 100.0
            if this_coverage.total_topics
            else None
        )
        if share is not None:
            feedback_note = f"{share:.1f}".replace(".", ",") + "% teemadest"
            if previous_coverage is not None and previous_coverage.total_topics:
                previous_share = (
                    previous_coverage.with_feedback / previous_coverage.total_topics * 100.0
                )
                pp_change = share - previous_share
                arrow = "↑" if pp_change > 0 else "↓" if pp_change < 0 else "→"
                pp_text = f"{pp_change:+.1f}".replace(".", ",")
                feedback_note += (
                    f" · {arrow} {pp_text} pp vs {year - 1} "
                    f"({integer(previous_coverage.with_feedback)}/"
                    f"{integer(previous_coverage.total_topics)})"
                )

    return (
        Headline(label=f"Teemasid {year}", value=integer(topics), note=arrivals_note),
        Headline(
            label=f"Arvamusi välja {year}",
            value=integer(sent),
            note=sent_note,
            change_label=sent_change_label,
            direction=sent_direction,
        ),
        # `Hetkel töös` rather than the brief's example wording. It is the same
        # measure — `is_open=True` — it is this product's established Estonian
        # for it, and the dashboard overview links into the section of that name,
        # so renaming the figure here would leave the two disagreeing.
        Headline(label="Hetkel töös", value=integer(active)),
        Headline(label="Liikmed andsid tagasisidet", value=feedback_value, note=feedback_note),
    )


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def build_page(snapshot, *, focus: str, page_url: str) -> IntelligencePage:
    """Assemble exactly the focus that was asked for."""
    focus = parse_focus(focus)
    label = dict(FOCUS_CHOICES)[focus]
    links = focus_links(page_url, focus)

    if snapshot is None:
        return IntelligencePage(focus=focus, focus_label=label, links=links)

    year = snapshot.reporting_date.year

    if focus == FOCUS_WORKFLOW:
        return _workflow(snapshot, year, focus, label, links)
    return _overview(snapshot, year, focus, label, links)


def _overview(snapshot, year, focus, label, links) -> IntelligencePage:
    """The KPI strip, the standing lists, and one chart of where the
    register's feedback lands — everything else that used to lead the page
    (the stage-breakdown chart, `Mis muutus?`, the feedback summary strip and
    its own breakdown tables) retired on 2026-08-18 along with the three
    focuses that carried them. The register explorer itself is not built
    here — `views.py` builds it unconditionally now, next to `Hetkel töös`
    and `Viimati välja läinud`, the same way it always read the other two.
    """
    by_recipient = feedback_breakdown(snapshot, "recipient")
    summary = feedback_summary(snapshot, year=year)
    feedback_charts = (
        (
            charts.feedback_category_chart(
                by_recipient,
                payload_id="legal-feedback-recipients",
                title="Tagasisidega teemad saaja järgi",
                category_header="Saaja",
            ),
        )
        if by_recipient and summary.tracked_topics >= MIN_FEEDBACK_TOPICS_FOR_BREAKDOWN
        else ()
    )
    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        headlines=_headlines(snapshot, year),
        charts=feedback_charts,
        deadlines=get_upcoming_deadlines(snapshot, limit=OVERVIEW_DEADLINE_LIMIT),
    )


def _workflow(snapshot, year, focus, label, links) -> IntelligencePage:
    """Both halves of the register's own throughput, in one scroll since
    2026-08-18: how topics arrive and opinions leave, month by month and year
    by year, who receives them, and how long a lawyer had to answer. This
    focus was `Töövoog` alone until then; `Arvamused` — the annual sent
    chart, the response-window median/mean and its distribution — merged in
    whole, and `annual_topics_chart`/`annual_sent_chart` merged into one
    combined `annual_activity_chart` that also carries the feedback-coverage
    line the standalone `Tagasiside` focus used to draw as a table.
    """
    current_topics = monthly_new_topics(snapshot, year)
    previous_topics = monthly_new_topics(snapshot, year - 1)
    current_sent = monthly_sent_opinions(snapshot, year)
    previous_sent = monthly_sent_opinions(snapshot, year - 1)
    windows = response_window_by_year(snapshot)

    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        monthly_charts=(
            charts.monthly_flow_chart(
                payload_id="legal-monthly-topics",
                title="Uued teemad kuude lõikes",
                current=current_topics,
                previous=previous_topics,
                series_label="Uued teemad",
            ),
            charts.monthly_flow_chart(
                payload_id="legal-monthly-sent",
                title="Välja saadetud arvamused kuude lõikes",
                current=current_sent,
                previous=previous_sent,
                series_label="Välja saadetud arvamused",
            ),
        ),
        charts=(
            charts.annual_activity_chart(
                annual_topics(snapshot),
                annual_sent_opinions(snapshot),
                feedback_coverage_by_year(snapshot),
            ),
            charts.category_chart(
                recipient_breakdown(snapshot),
                payload_id="legal-recipients",
                title="Kellele arvamusi oleme saatnud",
                category_header="Saaja",
            ),
            charts.response_window_distribution_chart(response_window_distribution(snapshot)),
            charts.response_window_chart(windows),
        ),
    )


def annual_topic_series(snapshot):
    """Exposed for the workflow view's long-term context table."""
    return annual_topics(snapshot)


def quality_detail(snapshot) -> tuple[DataQuality, tuple]:
    """`Andmete kohta`: coverage plus the warning-code tally behind it."""
    return data_quality(snapshot), warning_code_counts(snapshot)
