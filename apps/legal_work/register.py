"""The register explorer: filters over the whole snapshot, and record detail.

`search.py` owns the term-and-status search that the overview carries and the
live-search fragment answers. This module is the register focus's own surface:
the same rows, reached through the same selector, with the facets a lawyer
actually narrows by.

Two rules shape everything here:

- **nothing reaches the ORM unvalidated.** The closed-vocabulary filters are
  checked against their own tuples; the free-text facets — stage, recipient, act
  type, year — are checked against *the categories this snapshot actually
  contains*. A value that names nothing in the register is dropped rather than
  passed through, so the query string cannot smuggle a `LIKE` or an unbounded
  string into a filter;
- **an applied filter is always visible.** A narrowed register that looks
  unnarrowed is worse than no filter at all: the reader concludes the Chamber
  has eleven records. The advanced disclosure therefore reports how many
  filters are active even while collapsed, and every active one is drawn as a
  chip that removes it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import quote

from django.core.paginator import EmptyPage, Paginator

from .selectors import (
    DEADLINE_ANY,
    DEADLINE_FORTNIGHT,
    DEADLINE_NONE,
    DEADLINE_OVERDUE,
    DEADLINE_STATES,
    DEADLINE_THREE_WEEKS,
    DEADLINE_WEEK,
    FEEDBACK_ANY,
    FEEDBACK_PRESENT,
    FEEDBACK_STATES,
    FEEDBACK_UNTRACKED,
    FEEDBACK_ZERO,
    MAX_SEARCH_LENGTH,
    SEARCH_ALL,
    SEARCH_STATUSES,
    search_items,
)

PARAM_QUERY = "otsing"
PARAM_STATUS = "seis"
PARAM_PAGE = "lk"
PARAM_YEAR = "aasta"
PARAM_STAGE = "etapp"
PARAM_RECIPIENT = "saaja"
PARAM_ACT_TYPE = "liik"
PARAM_DEADLINE = "tahtaeg"
PARAM_FEEDBACK = "tagasiside"

#: Every parameter this surface understands, for the fragment's push-URL.
REGISTER_PARAMS = (
    PARAM_QUERY,
    PARAM_STATUS,
    PARAM_PAGE,
    PARAM_YEAR,
    PARAM_STAGE,
    PARAM_RECIPIENT,
    PARAM_ACT_TYPE,
    PARAM_DEADLINE,
    PARAM_FEEDBACK,
)

PER_PAGE = 25

#: How many distinct values a facet offers before it stops being a menu. The
#: register carries around thirty recipients across its history, so this is a
#: ceiling against a future snapshot rather than a trim of the current one.
MAX_FACET_OPTIONS = 60

DEADLINE_LABELS: tuple[tuple[str, str], ...] = (
    (DEADLINE_ANY, "Kõik"),
    (DEADLINE_OVERDUE, "Tähtaeg möödas, arvamus ootel"),
    (DEADLINE_WEEK, "Tähtaeg 7 päeva jooksul"),
    (DEADLINE_FORTNIGHT, "Tähtaeg 14 päeva jooksul"),
    (DEADLINE_THREE_WEEKS, "Tähtaeg 21 päeva jooksul"),
    (DEADLINE_NONE, "Tähtaeg puudub"),
)

FEEDBACK_LABELS: tuple[tuple[str, str], ...] = (
    (FEEDBACK_ANY, "Kõik"),
    (FEEDBACK_PRESENT, "Liikmed andsid tagasisidet"),
    (FEEDBACK_ZERO, "Mõõdetud, tagasisidet ei antud"),
    (FEEDBACK_UNTRACKED, "Tagasisidet ei ole mõõdetud"),
)

STATUS_LABELS: tuple[tuple[str, str], ...] = (
    (SEARCH_ALL, "Kõik"),
    ("toos", "Töös"),
    ("valjas", "Välja läinud"),
)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_text(raw: str | None) -> str:
    return (raw or "").strip()[:MAX_SEARCH_LENGTH]


def parse_choice(raw: str | None, allowed) -> str:
    value = (raw or "").strip()
    return value if value in allowed else ""


def parse_page(raw: str | int | None) -> int:
    try:
        return max(int(raw), 1)
    except TypeError, ValueError:
        return 1


def parse_year(raw: str | None, allowed_years) -> int | None:
    """A year, but only one the snapshot actually holds."""
    try:
        year = int((raw or "").strip())
    except TypeError, ValueError:
        return None
    return year if year in allowed_years else None


@dataclass(frozen=True)
class RegisterQuery:
    """The validated register state. Every field is safe to put in a filter."""

    query: str = ""
    status: str = SEARCH_ALL
    year: int | None = None
    stage_key: str = ""
    recipient: str = ""
    act_type: str = ""
    deadline: str = DEADLINE_ANY
    feedback: str = FEEDBACK_ANY
    page: int = 1

    @property
    def active_filter_count(self) -> int:
        """How many narrowing choices are in force, excluding the term itself.

        The term has its own visible box; these are the ones that can hide
        inside a collapsed disclosure and silently shrink the register.
        """
        return sum(
            1
            for value in (
                self.status,
                self.year,
                self.stage_key,
                self.recipient,
                self.act_type,
                self.deadline,
                self.feedback,
            )
            if value
        )

    @property
    def has_filters(self) -> bool:
        return self.active_filter_count > 0


def parse_register_query(params, facets: RegisterFacets) -> RegisterQuery:
    """Read the query string into validated state, or into defaults.

    The free-text facets are validated against `facets`, which is derived from
    the snapshot itself, so an unknown stage or a renamed ministry from an old
    bookmark simply does not apply rather than returning nothing.
    """
    return RegisterQuery(
        query=parse_text(params.get(PARAM_QUERY)),
        status=parse_choice(params.get(PARAM_STATUS), SEARCH_STATUSES),
        year=parse_year(params.get(PARAM_YEAR), facets.years),
        stage_key=parse_choice(params.get(PARAM_STAGE), facets.stage_keys),
        recipient=parse_choice(params.get(PARAM_RECIPIENT), facets.recipients),
        act_type=parse_choice(params.get(PARAM_ACT_TYPE), facets.act_types),
        deadline=parse_choice(params.get(PARAM_DEADLINE), DEADLINE_STATES),
        feedback=parse_choice(params.get(PARAM_FEEDBACK), FEEDBACK_STATES),
        page=parse_page(params.get(PARAM_PAGE)),
    )


# --------------------------------------------------------------------------
# Facets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FacetOption:
    value: str
    label: str
    is_active: bool
    query: str


@dataclass(frozen=True)
class RegisterFacets:
    """The categories this snapshot actually contains.

    Read from the register rather than hard-coded, because the stage vocabulary
    is free text that gains entries between one workbook and the next, and a
    menu of last month's stages would hide this month's work. Four grouped
    queries, none of them per row.
    """

    years: tuple[int, ...] = ()
    stages: tuple[tuple[str, str], ...] = ()
    recipients: tuple[str, ...] = ()
    act_types: tuple[str, ...] = ()

    @property
    def stage_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _label in self.stages)


def build_facets(snapshot) -> RegisterFacets:
    """Every value the register offers to narrow by, in one pass per facet."""
    if snapshot is None:
        return RegisterFacets()

    from django.db.models import Count

    items = snapshot.items

    years = tuple(items.values_list("source_year", flat=True).order_by("-source_year").distinct())

    # The label is the commonest spelling inside each key, resolved from the
    # same grouped rows rather than by a second query per stage.
    stage_rows = (
        items.exclude(stage_key="")
        .values("stage_key", "stage")
        .annotate(count=Count("id"))
        .order_by("stage_key", "-count")
    )
    stages: dict[str, str] = {}
    for row in stage_rows:
        stages.setdefault(row["stage_key"], (row["stage"] or "").strip() or row["stage_key"])

    recipients = tuple(
        row["recipient"]
        for row in items.exclude(recipient="")
        .values("recipient")
        .annotate(count=Count("id"))
        .order_by("-count", "recipient")[:MAX_FACET_OPTIONS]
    )
    act_types = tuple(
        row["act_type"]
        for row in items.exclude(act_type="")
        .values("act_type")
        .annotate(count=Count("id"))
        .order_by("-count", "act_type")[:MAX_FACET_OPTIONS]
    )

    return RegisterFacets(
        years=years,
        stages=tuple(sorted(stages.items(), key=lambda pair: pair[1].lower())),
        recipients=recipients,
        act_types=act_types,
    )


# --------------------------------------------------------------------------
# URL state
# --------------------------------------------------------------------------


def build_query(state: RegisterQuery, **overrides) -> str:
    """One URL's worth of register state, from validated values only.

    Every control links through here, which is what makes them compose:
    choosing a recipient keeps the term, the year and the status, and paging
    keeps all of them. Editing a copy of `request.GET` would carry whatever else
    happened to be in the address, including a page number that no longer
    exists.

    Any change other than the page itself resets to page one, because a reader
    on page three of one question is asking a new question when they narrow it,
    and page three of the narrower answer is usually empty.
    """
    state = replace(state, **overrides)
    if set(overrides) - {"page"}:
        state = replace(state, page=1)

    parts: list[str] = []
    for key, value in (
        (PARAM_QUERY, state.query),
        (PARAM_STATUS, state.status),
        (PARAM_YEAR, state.year),
        (PARAM_STAGE, state.stage_key),
        (PARAM_RECIPIENT, state.recipient),
        (PARAM_ACT_TYPE, state.act_type),
        (PARAM_DEADLINE, state.deadline),
        (PARAM_FEEDBACK, state.feedback),
    ):
        if value:
            parts.append(f"{key}={quote(str(value))}")
    if state.page > 1:
        parts.append(f"{PARAM_PAGE}={state.page}")
    return "&".join(parts)


@dataclass(frozen=True)
class AppliedFilter:
    """One filter in force, and the address that removes it."""

    label: str
    value: str
    remove_query: str


def _applied(state: RegisterQuery, facets: RegisterFacets) -> tuple[AppliedFilter, ...]:
    applied: list[AppliedFilter] = []

    if state.status:
        label = dict(STATUS_LABELS).get(state.status, state.status)
        applied.append(AppliedFilter("Olek", label, build_query(state, status=SEARCH_ALL)))
    if state.year is not None:
        applied.append(AppliedFilter("Aasta", str(state.year), build_query(state, year=None)))
    if state.stage_key:
        label = dict(facets.stages).get(state.stage_key, state.stage_key)
        applied.append(AppliedFilter("Hetkeseis", label, build_query(state, stage_key="")))
    if state.recipient:
        applied.append(AppliedFilter("Saaja", state.recipient, build_query(state, recipient="")))
    if state.act_type:
        applied.append(
            AppliedFilter("Õigusakti liik", state.act_type, build_query(state, act_type=""))
        )
    if state.deadline:
        label = dict(DEADLINE_LABELS).get(state.deadline, state.deadline)
        applied.append(AppliedFilter("Tähtaeg", label, build_query(state, deadline=DEADLINE_ANY)))
    if state.feedback:
        label = dict(FEEDBACK_LABELS).get(state.feedback, state.feedback)
        applied.append(
            AppliedFilter("Tagasiside", label, build_query(state, feedback=FEEDBACK_ANY))
        )
    return tuple(applied)


def _options(state: RegisterQuery, field: str, pairs) -> tuple[FacetOption, ...]:
    active = getattr(state, field)
    return tuple(
        FacetOption(
            value=str(value),
            label=label,
            is_active=str(value) == str(active or ""),
            query=build_query(state, **{field: value or ("" if field != "year" else None)}),
        )
        for value, label in pairs
    )


# --------------------------------------------------------------------------
# The register view
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordDetail:
    """One register row with everything its drill-down draws.

    `resource_url` is the single address the domain's own rule resolved for this
    record — the opinion when it has been sent, the live consultation when it is
    still open and unanswered, the archive entry as a fallback, and nothing at
    all when no trustworthy match exists. `resource_is_opinion` says which of
    those it is so the link can be labelled honestly; showing a consultation
    beside an answered matter would tell a reader the opposite of the truth.
    """

    item: object
    resource_url: str = ""
    resource_is_opinion: bool = False

    @property
    def topic(self) -> str:
        return self.item.topic

    @property
    def public_url(self) -> str:
        return self.resource_url

    @property
    def has_resource(self) -> bool:
        return bool(self.resource_url)

    @property
    def timeline(self) -> tuple[tuple[str, object], ...]:
        """`Sisse → Tähtaeg → Välja`, carrying only the steps that happened.

        A missing date is left out rather than drawn as an empty slot: the
        register genuinely does not know when some matters arrived, and an
        invented placeholder would read as a date nobody recorded.
        """
        steps = []
        if self.item.received_date:
            steps.append(("Sisse", self.item.received_date))
        if self.item.deadline_date:
            steps.append(("Tähtaeg", self.item.deadline_date))
        if self.item.sent_date:
            steps.append(("Välja", self.item.sent_date))
        return tuple(steps)

    @property
    def has_warnings(self) -> bool:
        return bool(self.item.warning_codes)


@dataclass(frozen=True)
class RegisterView:
    """Everything the register focus renders."""

    state: RegisterQuery
    facets: RegisterFacets
    applied: tuple[AppliedFilter, ...] = ()
    status_options: tuple[FacetOption, ...] = ()
    year_options: tuple[FacetOption, ...] = ()
    stage_options: tuple[FacetOption, ...] = ()
    recipient_options: tuple[FacetOption, ...] = ()
    act_type_options: tuple[FacetOption, ...] = ()
    deadline_options: tuple[FacetOption, ...] = ()
    feedback_options: tuple[FacetOption, ...] = ()
    records: tuple[RecordDetail, ...] = ()
    total: int = 0
    page_number: int = 1
    total_pages: int = 1

    @property
    def has_records(self) -> bool:
        return bool(self.records)

    @property
    def has_previous(self) -> bool:
        return self.page_number > 1

    @property
    def has_next(self) -> bool:
        return self.page_number < self.total_pages

    @property
    def previous_query(self) -> str:
        return build_query(self.state, page=max(self.page_number - 1, 1))

    @property
    def next_query(self) -> str:
        return build_query(self.state, page=min(self.page_number + 1, max(self.total_pages, 1)))

    @property
    def clear_query(self) -> str:
        return ""

    @property
    def summary(self) -> str:
        if not self.total:
            return "Ühtegi kirjet ei leitud."
        if self.total == 1:
            return "1 kirje."
        return f"{self.total} kirjet."


def build_register(snapshot, params) -> RegisterView:
    """The register focus, resolved from the query string in one pass."""
    facets = build_facets(snapshot)
    state = parse_register_query(params, facets)

    queryset = search_items(
        snapshot,
        query=state.query,
        status=state.status,
        source_year=state.year,
        stage_key=state.stage_key,
        recipient=state.recipient,
        act_type=state.act_type,
        deadline=state.deadline,
        feedback=state.feedback,
    )
    paginator = Paginator(queryset, PER_PAGE)
    try:
        current = paginator.page(state.page)
    except EmptyPage:
        current = paginator.page(paginator.num_pages)

    rows = tuple(current.object_list)

    return RegisterView(
        state=replace(state, page=current.number),
        facets=facets,
        applied=_applied(state, facets),
        status_options=_options(state, "status", STATUS_LABELS),
        year_options=_options(state, "year", tuple((year, str(year)) for year in facets.years)),
        stage_options=_options(state, "stage_key", facets.stages),
        recipient_options=_options(
            state, "recipient", tuple((value, value) for value in facets.recipients)
        ),
        act_type_options=_options(
            state, "act_type", tuple((value, value) for value in facets.act_types)
        ),
        deadline_options=_options(state, "deadline", DEADLINE_LABELS),
        feedback_options=_options(state, "feedback", FEEDBACK_LABELS),
        records=_detail_for(rows),
        total=paginator.count,
        page_number=current.number,
        total_pages=paginator.num_pages,
    )


def _detail_for(rows) -> tuple[RecordDetail, ...]:
    """Attach each row's resolved resource, in two bulk lookups for the page.

    Not one lookup per row. The page draws twenty-five records and asks the
    matcher twice, which is what keeps the register's cost independent of how
    many rows it happens to show.
    """
    from .topic_links import resolve_consultation_links, resolve_opinion_links

    if not rows:
        return ()

    ids = [row.pk for row in rows]
    resolved = resolve_consultation_links(ids)
    opinions = resolve_opinion_links(ids)

    return tuple(
        RecordDetail(
            item=row,
            resource_url=resolved.get(row.pk, ""),
            resource_is_opinion=row.pk in opinions,
        )
        for row in rows
    )
