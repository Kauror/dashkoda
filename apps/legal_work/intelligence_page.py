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
    first_tracked_feedback_year,
    monthly_new_topics,
    monthly_sent_opinions,
    recipient_breakdown,
    response_window_by_year,
    response_window_distribution,
    sent_year_on_year,
    stage_breakdown,
    top_feedback_topics,
    topics_year_on_year,
    warning_code_counts,
)
from .selectors import (
    get_upcoming_deadlines,
)

PARAM_FOCUS = "fookus"

FOCUS_OVERVIEW = "ulevaade"
FOCUS_WORKFLOW = "toovoog"
FOCUS_OPINIONS = "arvamused"
FOCUS_FEEDBACK = "tagasiside"
FOCUS_REGISTER = "register"

#: The closed set, in the order the navigation draws it. A value outside it is
#: not an error the reader caused on purpose — a truncated link, an old
#: bookmark — so it resolves to the overview instead of a 404. `aktiivsed`
#: left the set on 2026-08-17; a link still carrying it resolves here too,
#: same as any other stale value.
FOCUS_CHOICES: tuple[tuple[str, str], ...] = (
    (FOCUS_OVERVIEW, "Ülevaade"),
    (FOCUS_WORKFLOW, "Töövoog"),
    (FOCUS_OPINIONS, "Arvamused"),
    (FOCUS_FEEDBACK, "Liikmete tagasiside"),
    (FOCUS_REGISTER, "Register"),
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
class Insight:
    """One deterministic observation for `Mis muutus?`.

    Every one is a comparison the data supports, written as a measurement. No
    generated prose, no causal claim and no score: the dashboard supplies the
    evidence and the reader supplies the interpretation.
    """

    label: str
    detail: str
    direction: str = ""


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
    secondary: tuple[Headline, ...] = ()
    insights: tuple[Insight, ...] = ()
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

    `{aasta}. aasta teemad` deliberately carries no year-on-year delta. It is a
    stock counted by the register's own annual grouping, which keeps growing
    until the year closes, so it cannot be clamped to a comparable date the way
    a datable event can. Attaching a delta to it would compare a part-year stock
    with a finished one — exactly the comparison this dashboard exists to avoid.
    """
    from apps.core.formatting import integer

    sent_change = sent_year_on_year(snapshot)
    topics = count_topics_for_year(snapshot, year)
    sent = count_sent_in_year(snapshot, year, cutoff=snapshot.reporting_date)
    active = stage_breakdown(snapshot).total

    yoy = charts.year_on_year_readout("Arvamuste muutus võrreldes eelmise aastaga", sent_change)

    return (
        Headline(
            label=f"{year}. aasta teemasid kokku",
            value=integer(topics),
        ),
        Headline(
            label=f"{year}. aastal arvamusi välja",
            value=integer(sent),
        ),
        Headline(
            label="Arvamuste muutus võrreldes eelmise aastaga",
            value=yoy.change or "–",
            note=(
                f"{integer(sent_change.current)} vs {integer(sent_change.previous)}"
                if sent_change is not None
                else ""
            ),
            change_label=yoy.change_label,
            direction=yoy.direction,
        ),
        # `Hetkel töös` rather than the brief's example wording. It is the same
        # measure — `is_open=True` — it is this product's established Estonian
        # for it, and the dashboard overview links into the section of that name,
        # so renaming the figure here would leave the two disagreeing.
        Headline(
            label="Hetkel töös",
            value=integer(active),
        ),
    )


def _secondary(snapshot, year: int) -> tuple[Headline, ...]:
    """The one smaller readout, kept off the hero row.

    It is a real measurement rather than a field that happened to exist.

    `Sisse tulnud sel aastal` and `Tähtaeg 7 päeva jooksul` left on 2026-08-16,
    and their selectors went with them: this function no longer calls
    `topics_year_on_year` or `deadline_pressure`, because a figure nothing
    renders is a query nobody needed. `topics_year_on_year` is still called
    from `_insights`; `deadline_pressure` is untouched and still tested, and
    still drives the executive overview's own reading — see
    `apps.legal_work.executive`.
    """
    windows = {entry.year: entry for entry in response_window_by_year(snapshot)}
    this_year = windows.get(year)

    readouts: list[Headline] = []
    if this_year is not None and this_year.median is not None:
        readouts.append(
            Headline(
                label="Mediaan arvamuse esitamiseks antud päevi",
                value=f"{this_year.median:.0f}",
            )
        )
    return tuple(readouts)


def _insights(snapshot, year: int) -> tuple[Insight, ...]:
    """`Mis muutus?` — only comparisons the data can actually support.

    `Arvamusi välja saadetud`, `Arvamuse esitamiseks antud aeg` and
    `Lähenevad tähtajad` left this section on 2026-08-17; each stated a
    figure the headline strip or the response-time chart already carried.
    `sent_year_on_year` is still called from `_headlines` and
    `response_window_by_year` from `_secondary`, so nothing computed for
    them is lost, only their repetition here. The `Aktiivsed teemad` focus
    that `Lähenevad tähtajad` also echoed left the page the same day — see
    `FOCUS_CHOICES`.
    """
    from apps.core.formatting import integer, signed_integer

    found: list[Insight] = []

    arrivals = topics_year_on_year(snapshot)
    if arrivals is not None and arrivals.previous:
        found.append(
            Insight(
                label="Uusi teemasid sisse",
                detail=(
                    f"{integer(arrivals.current)} sel aastal seisuga "
                    f"{arrivals.current_cutoff:%d.%m.%Y}, eelmisel aastal sama "
                    f"kuupäevani {integer(arrivals.previous)} "
                    f"({signed_integer(arrivals.absolute_change)})."
                ),
                direction=arrivals.direction,
            )
        )

    return tuple(found)


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

    if focus == FOCUS_OVERVIEW:
        return _overview(snapshot, year, focus, label, links)
    if focus == FOCUS_WORKFLOW:
        return _workflow(snapshot, year, focus, label, links)
    if focus == FOCUS_OPINIONS:
        return _opinions(snapshot, year, focus, label, links)
    if focus == FOCUS_FEEDBACK:
        return _feedback(snapshot, year, focus, label, links)
    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        quality=data_quality(snapshot),
    )


def _overview(snapshot, year, focus, label, links) -> IntelligencePage:
    stages = stage_breakdown(snapshot)
    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        headlines=_headlines(snapshot, year),
        secondary=_secondary(snapshot, year),
        insights=_insights(snapshot, year),
        stage=stages,
        charts=(charts.active_stage_chart(stages),),
        deadlines=get_upcoming_deadlines(snapshot, limit=OVERVIEW_DEADLINE_LIMIT),
        # The feedback preview left the overview on 2026-08-16: it was the
        # `tagasiside` focus's own strip rendered a click early, so this focus
        # no longer runs its query either.
    )


def _workflow(snapshot, year, focus, label, links) -> IntelligencePage:
    current_topics = monthly_new_topics(snapshot, year)
    previous_topics = monthly_new_topics(snapshot, year - 1)
    current_sent = monthly_sent_opinions(snapshot, year)
    previous_sent = monthly_sent_opinions(snapshot, year - 1)

    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        charts=(
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
            charts.annual_topics_chart(annual_topics(snapshot)),
            # `Enim esinevad õigusakti liigid` — the act-type breakdown chart
            # — left this focus entirely on 2026-08-17, and `act_type_breakdown`
            # went with it: a one-line wrapper over `_category_breakdown`
            # with no other caller and no test of its own.
            charts.category_chart(
                recipient_breakdown(snapshot),
                payload_id="legal-recipients",
                title="Kellele arvamusi oleme saatnud",
                category_header="Saaja",
            ),
        ),
        # `Kuidas neid arve lugeda` and its one caveat left with it, on the
        # same day.
    )


def _opinions(snapshot, year, focus, label, links) -> IntelligencePage:
    comparison = sent_year_on_year(snapshot)
    windows = response_window_by_year(snapshot)

    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        charts=(
            charts.annual_sent_chart(annual_sent_opinions(snapshot), comparison),
            charts.response_window_chart(windows),
            charts.response_window_distribution_chart(response_window_distribution(snapshot)),
        ),
        # `Viimati välja läinud` renders on the overview alone since
        # 2026-08-16 — this focus repeated it — so the query goes too.
        # `Kuidas neid arve lugeda` and its on-time-submission footnotes left
        # on 2026-08-17; `sent_by_deadline` is untouched and still tested,
        # just no longer called here.
    )


def _feedback(snapshot, year, focus, label, links) -> IntelligencePage:
    summary = feedback_summary(snapshot, year=year)
    start_year = first_tracked_feedback_year(snapshot)

    # Only drawn once there is something to describe. A breakdown built from two
    # measured topics would rank noise, and an empty bar chart beside a
    # "measurement is still starting" note contradicts the note.
    breakdown_charts: list = []
    if summary.tracked_topics >= MIN_FEEDBACK_TOPICS_FOR_BREAKDOWN:
        by_act_type = feedback_breakdown(snapshot, "act_type")
        by_recipient = feedback_breakdown(snapshot, "recipient")
        if by_act_type:
            breakdown_charts.append(
                charts.feedback_category_chart(
                    by_act_type,
                    payload_id="legal-feedback-act-types",
                    title="Milliste õigusaktide teemadel liikmed tagasisidet annavad",
                    category_header="Õigusakti liik",
                )
            )
        if by_recipient:
            breakdown_charts.append(
                charts.feedback_category_chart(
                    by_recipient,
                    payload_id="legal-feedback-recipients",
                    title="Tagasisidega teemad saaja järgi",
                    category_header="Saaja",
                )
            )

    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        feedback=summary,
        feedback_coverage=feedback_coverage_by_year(snapshot),
        feedback_start_year=start_year,
        feedback_topics=top_feedback_topics(snapshot),
        charts=tuple(breakdown_charts),
        # `Kuidas neid arve lugeda` and its four caveats left on 2026-08-17.
        # `start_year` still reaches the page — see `feedback_start_year`
        # above — so a reader can still see where the series begins.
    )


def annual_topic_series(snapshot):
    """Exposed for the workflow view's long-term context table."""
    return annual_topics(snapshot)


def quality_detail(snapshot) -> tuple[DataQuality, tuple]:
    """`Andmete kohta`: coverage plus the warning-code tally behind it."""
    return data_quality(snapshot), warning_code_counts(snapshot)
