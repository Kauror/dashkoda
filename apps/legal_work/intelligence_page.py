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
    ActiveAge,
    DataQuality,
    DeadlinePressure,
    FeedbackSummary,
    StageBreakdown,
    act_type_breakdown,
    active_topic_age,
    annual_sent_opinions,
    annual_topics,
    count_sent_in_year,
    count_topics_for_year,
    data_quality,
    deadline_pressure,
    feedback_breakdown,
    feedback_coverage_by_year,
    feedback_summary,
    first_tracked_feedback_year,
    monthly_new_topics,
    monthly_sent_opinions,
    recipient_breakdown,
    response_window_by_year,
    response_window_distribution,
    sent_by_deadline,
    sent_year_on_year,
    stage_breakdown,
    top_feedback_topics,
    topics_year_on_year,
    warning_code_counts,
)
from .selectors import (
    DEFAULT_RECENT_LIMIT,
    get_latest_sent_items,
    get_open_items_by_deadline,
    get_upcoming_deadlines,
)

PARAM_FOCUS = "fookus"

FOCUS_OVERVIEW = "ulevaade"
FOCUS_WORKFLOW = "toovoog"
FOCUS_ACTIVE = "aktiivsed"
FOCUS_OPINIONS = "arvamused"
FOCUS_FEEDBACK = "tagasiside"
FOCUS_REGISTER = "register"

#: The closed set, in the order the navigation draws it. A value outside it is
#: not an error the reader caused on purpose — a truncated link, an old
#: bookmark — so it resolves to the overview instead of a 404.
FOCUS_CHOICES: tuple[tuple[str, str], ...] = (
    (FOCUS_OVERVIEW, "Ülevaade"),
    (FOCUS_WORKFLOW, "Töövoog"),
    (FOCUS_ACTIVE, "Aktiivsed teemad"),
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
    open_items: tuple = ()
    sent_items: tuple = ()
    stage: StageBreakdown | None = None
    age: ActiveAge | None = None
    pressure: DeadlinePressure | None = None
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
            note="Aktiivsed teemad hetkeseisuga",
        ),
    )


def _secondary(snapshot, year: int) -> tuple[Headline, ...]:
    """The one smaller readout, kept off the hero row.

    It is a real measurement rather than a field that happened to exist.

    `Sisse tulnud sel aastal` and `Tähtaeg 7 päeva jooksul` left on 2026-08-16,
    and their selectors went with them: this function no longer calls
    `topics_year_on_year` or `deadline_pressure`, because a figure nothing
    renders is a query nobody needed. Both selectors are untouched and still
    tested, and `deadline_pressure` still drives the `Lähenevad tähtajad`
    insight from `_insights`.
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
    """`Mis muutus?` — only comparisons the data can actually support."""
    from apps.core.formatting import integer, signed_integer

    found: list[Insight] = []

    sent_change = sent_year_on_year(snapshot)
    if sent_change is not None and sent_change.previous:
        found.append(
            Insight(
                label="Arvamusi välja saadetud",
                detail=(
                    f"{integer(sent_change.current)} sel aastal seisuga "
                    f"{sent_change.current_cutoff:%d.%m.%Y}, eelmisel aastal sama "
                    f"kuupäevani {integer(sent_change.previous)} "
                    f"({signed_integer(sent_change.absolute_change)})."
                ),
                direction=sent_change.direction,
            )
        )

    arrivals = topics_year_on_year(snapshot)
    if arrivals is not None and arrivals.previous:
        found.append(
            Insight(
                label="Uusi teemasid saabunud",
                detail=(
                    f"{integer(arrivals.current)} sel aastal seisuga "
                    f"{arrivals.current_cutoff:%d.%m.%Y}, eelmisel aastal sama "
                    f"kuupäevani {integer(arrivals.previous)} "
                    f"({signed_integer(arrivals.absolute_change)})."
                ),
                direction=arrivals.direction,
            )
        )

    windows = {entry.year: entry for entry in response_window_by_year(snapshot)}
    this_year, last_year = windows.get(year), windows.get(year - 1)
    if this_year and last_year and this_year.median is not None and last_year.median is not None:
        found.append(
            Insight(
                label="Arvamuse esitamiseks antud aeg",
                detail=(
                    f"{year}. aasta mediaan on {this_year.median:.0f} päeva, "
                    f"{year - 1}. aastal {last_year.median:.0f} päeva."
                ),
                direction="down" if this_year.median < last_year.median else "up",
            )
        )

    pressure = deadline_pressure(snapshot)
    if pressure.due_within_7:
        found.append(
            Insight(
                label="Lähenevad tähtajad",
                detail=(
                    f"{integer(pressure.due_within_7)} aktiivsel teemal on tähtaeg "
                    "seitsme päeva jooksul."
                ),
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
    if focus == FOCUS_ACTIVE:
        return _active(snapshot, year, focus, label, links)
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
        feedback=feedback_summary(snapshot, year=year),
        feedback_start_year=first_tracked_feedback_year(snapshot),
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
                question="Kui palju uut tööd Kojale saabub?",
                current=current_topics,
                previous=previous_topics,
                series_label="Uued teemad",
            ),
            charts.monthly_flow_chart(
                payload_id="legal-monthly-sent",
                title="Välja saadetud arvamused kuude lõikes",
                question="Kui palju arvamusi Koda välja saadab?",
                current=current_sent,
                previous=previous_sent,
                series_label="Välja saadetud arvamused",
            ),
            charts.annual_topics_chart(annual_topics(snapshot)),
            charts.category_chart(
                act_type_breakdown(snapshot),
                payload_id="legal-act-types",
                title="Enim esinevad õigusakti liigid",
                question="Milliste õigusaktidega Koda kõige rohkem tegeleb?",
                category_header="Õigusakti liik",
            ),
            charts.category_chart(
                recipient_breakdown(snapshot),
                payload_id="legal-recipients",
                title="Kellele arvamusi saadetakse?",
                question="Millistele asutustele Koja töö suundub?",
                category_header="Saaja",
            ),
        ),
        footnotes=(
            "Sisse tulnud teemad ja välja saadetud arvamused on kaks eraldi mõõdikut. "
            "Üks saabunud teema ei tähenda tingimata ühte arvamust ja arvamuse "
            "saatmine ei sulge teemat.",
        ),
    )


def _active(snapshot, year, focus, label, links) -> IntelligencePage:
    stages = stage_breakdown(snapshot)
    age = active_topic_age(snapshot)
    pressure = deadline_pressure(snapshot)
    return IntelligencePage(
        focus=focus,
        focus_label=label,
        links=links,
        reporting_year=year,
        stage=stages,
        age=age,
        pressure=pressure,
        charts=(
            charts.active_stage_chart(stages),
            charts.active_age_chart(age),
            charts.deadline_pressure_chart(pressure),
        ),
        open_items=tuple(get_open_items_by_deadline(snapshot)),
        deadlines=get_upcoming_deadlines(snapshot),
    )


def _opinions(snapshot, year, focus, label, links) -> IntelligencePage:
    from apps.core.formatting import integer, percent

    comparison = sent_year_on_year(snapshot)
    windows = response_window_by_year(snapshot)
    timing = sent_by_deadline(snapshot)

    footnotes = [
        "„Arvamus saadetud hiljemalt märgitud tähtajaks“ kirjeldab kuupäevi, mitte "
        "kellegi tööd: tähtaegu lepitakse kokku, lähteandmete kuupäevi täpsustatakse "
        "hiljem ja osa arvamusi esitatakse teadlikult pärast tähtaega.",
    ]
    if timing.eligible:
        footnotes.insert(
            0,
            f"Arvamus saadetud hiljemalt märgitud tähtajaks: "
            f"{integer(timing.on_or_before)} / {integer(timing.eligible)} "
            f"({percent(timing.share_on_or_before)}).",
        )

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
        sent_items=tuple(get_latest_sent_items(snapshot, limit=DEFAULT_RECENT_LIMIT)),
        footnotes=tuple(footnotes),
    )


def _feedback(snapshot, year, focus, label, links) -> IntelligencePage:
    summary = feedback_summary(snapshot, year=year)
    start_year = first_tracked_feedback_year(snapshot)

    footnotes = [
        "Allikas salvestab arvud, mitte isikuid: kui palju liikmeid vastas ja kui "
        "paljudelt otse küsiti. Liikmete nimesid ega ettevõtteid siin ei ole.",
        "Tegemist ei ole unikaalsete liikmetega — sama liige võib anda tagasisidet "
        "mitmel teemal ja läheb igal teemal eraldi arvesse.",
        "Vastamismäära ei arvutata: tagasisidet andnud liikmed ei ole otse küsitute "
        "alamhulk, sest liikmed vastavad ka uudiskirja ja üldiste pöördumiste kaudu.",
    ]
    if start_year is not None:
        footnotes.insert(
            0,
            f"Liikmete tagasiside mõõtmine algab registris {start_year}. aastast. "
            "Varasemaid aastaid ei kuvata nullina.",
        )

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
                    question="Kus liikmete osalus koondub?",
                    category_header="Õigusakti liik",
                )
            )
        if by_recipient:
            breakdown_charts.append(
                charts.feedback_category_chart(
                    by_recipient,
                    payload_id="legal-feedback-recipients",
                    title="Tagasisidega teemad saaja järgi",
                    question="Milliste asutuste teemadel liikmed osalevad?",
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
        footnotes=tuple(footnotes),
    )


def annual_topic_series(snapshot):
    """Exposed for the workflow view's long-term context table."""
    return annual_topics(snapshot)


def quality_detail(snapshot) -> tuple[DataQuality, tuple]:
    """`Andmete kohta`: coverage plus the warning-code tally behind it."""
    return data_quality(snapshot), warning_code_counts(snapshot)
