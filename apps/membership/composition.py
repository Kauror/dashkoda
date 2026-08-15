"""How a member roster becomes aggregate composition facts.

The Chamber's roster is a spreadsheet with one row per member organisation,
carrying the company's name, its registry code, its address, its director and
two contact addresses. **None of that may ever reach this application.** What
the dashboard needs is how many members are in each size class, county, sector,
tenure band and joining year — counts, and nothing that identifies whom they
count.

So this module is the boundary. It takes rows in and gives aggregates out, and
the models it feeds have no field capable of holding an identity. An importer
reads a row, asks the functions here which buckets it belongs in, adds one to
each, and discards it. Nothing here returns a row, retains a row, or writes one
anywhere.

Nothing in this module touches the database or Django, which is what lets the
whole classification be tested against synthetic rows without PostgreSQL.

Four classification rules are worth stating outright:

- **an unrecognised value is `unknown`, never the nearest neighbour.** A status
  the Chamber adds next year must appear as unclassified and be counted in the
  mapping-coverage figure, not be folded silently into `Koja liige`;
- **zero employees is its own band.** Putting a nought into `1–9` would be a
  guess dressed as a measurement, and fifteen rows in the current roster
  actually report it;
- **tenure is measured from the snapshot date, not from today.** A snapshot read
  in June and displayed in December must not gain six months of tenure by being
  looked at;
- **a category with too few members is suppressed, not zeroed.** A ratio built
  on three organisations is noise, and ranking it beside a category of eight
  hundred would present that noise as a finding.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# The sector classification in force. Bumped whenever a division moves between
# sections, so a stored aggregate always says which vocabulary produced it and
# two vintages can never be silently drawn as one series.
MEMBERSHIP_SECTOR_MAPPING_VERSION = "v1"

# The whole classification vocabulary in force, of which the sector mapping is
# one part. The importer stores it on the snapshot.
MEMBERSHIP_COMPOSITION_MAPPING_VERSION = "v1"

# How recent a membership start has to be to count as a recent joiner.
#
# Named in days rather than "a year" because the population is defined against
# the snapshot date, and a year is not a fixed number of days.
RECENT_JOINER_WINDOW_DAYS = 365


class Dimension:
    """The axes composition is counted along."""

    STATUS = "status"
    LEGAL_FORM = "legal_form"
    EMPLOYEE_SIZE = "employee_size"
    REGION = "region"
    SECTOR = "sector"
    TENURE_BAND = "tenure_band"
    JOIN_COHORT = "join_cohort"


DIMENSION_LABELS: dict[str, str] = {
    Dimension.STATUS: "Staatus",
    Dimension.LEGAL_FORM: "Õiguslik vorm",
    Dimension.EMPLOYEE_SIZE: "Ettevõtte suurus",
    Dimension.REGION: "Piirkond",
    Dimension.SECTOR: "Tegevusala",
    Dimension.TENURE_BAND: "Liikmestaaž",
    Dimension.JOIN_COHORT: "Liitumisaasta",
}

DIMENSIONS: tuple[str, ...] = tuple(DIMENSION_LABELS)


class Population:
    """Which members a set of counts describes.

    `RECENT_JOINERS` is deliberately long-winded, because the short version
    would be a lie. It is *members present in this snapshot whose recorded start
    date falls inside the window* — not everyone who joined during the year.
    Anyone who joined and left again before the snapshot is simply not in the
    roster, and no source in this application records them.
    """

    ALL_CURRENT = "all_current"
    RECENT_JOINERS = "recent_joiners_365_current"


POPULATION_LABELS: dict[str, str] = {
    Population.ALL_CURRENT: "Kõik praegused liikmed",
    Population.RECENT_JOINERS: "Viimase 12 kuu jooksul liitunud tänased liikmed",
}

POPULATIONS: tuple[str, ...] = tuple(POPULATION_LABELS)

#: The value every dimension uses for "the source did not say".
UNKNOWN = "unknown"
UNKNOWN_LABEL = "Teadmata"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

# The literal statuses the roster uses, mapped explicitly. A value not on this
# list becomes `unknown` and is counted in `unmapped_status`, which is what puts
# it in front of a person instead of into a neighbouring category.
STATUS_KEYS: dict[str, str] = {
    "koja liige": "regular",
    "peatatud liige": "suspended",
    "toetaja liige": "supporter",
}

STATUS_LABELS: dict[str, str] = {
    "regular": "Koja liige",
    "suspended": "Peatatud liige",
    "supporter": "Toetaja liige",
    UNKNOWN: UNKNOWN_LABEL,
}

STATUS_ORDER: tuple[str, ...] = ("regular", "suspended", "supporter", UNKNOWN)


def classify_status(raw) -> str:
    if raw is None:
        return UNKNOWN
    return STATUS_KEYS.get(str(raw).strip().casefold(), UNKNOWN)


# ---------------------------------------------------------------------------
# Legal form
# ---------------------------------------------------------------------------

LEGAL_FORM_LABELS: dict[str, str] = {
    "ou": "OÜ",
    "as": "AS",
    "mtu": "MTÜ",
    "sa": "SA",
    "tuu": "TuÜ",
    "fie": "FIE",
    UNKNOWN: UNKNOWN_LABEL,
}

# Written without diacritics on the left so a stray encoding cannot miss a match.
LEGAL_FORM_KEYS: dict[str, str] = {
    "oü": "ou",
    "ou": "ou",
    "as": "as",
    "mtü": "mtu",
    "mtu": "mtu",
    "sa": "sa",
    "tuü": "tuu",
    "tuu": "tuu",
    "fie": "fie",
}

LEGAL_FORM_ORDER: tuple[str, ...] = ("ou", "as", "mtu", "sa", "tuu", "fie", UNKNOWN)


def classify_legal_form(raw) -> str:
    if raw is None:
        return UNKNOWN
    return LEGAL_FORM_KEYS.get(str(raw).strip().casefold(), UNKNOWN)


# ---------------------------------------------------------------------------
# Company size
# ---------------------------------------------------------------------------

# The canonical analytical groups. These are the Eurostat size classes, which is
# what makes a Chamber size distribution comparable with published Estonian
# business statistics.
#
# `employees_0` is separate on purpose. A registered company with no employees
# is a real and different thing from one with a handful, and folding it into
# `1–9` would inflate the smallest class with organisations that do not belong
# in it.
SIZE_ZERO = "employees_0"
SIZE_1_9 = "employees_1_9"
SIZE_10_49 = "employees_10_49"
SIZE_50_249 = "employees_50_249"
SIZE_250_PLUS = "employees_250_plus"

SIZE_LABELS: dict[str, str] = {
    SIZE_ZERO: "Töötajateta",
    SIZE_1_9: "1–9 töötajat",
    SIZE_10_49: "10–49 töötajat",
    SIZE_50_249: "50–249 töötajat",
    SIZE_250_PLUS: "250+ töötajat",
    UNKNOWN: UNKNOWN_LABEL,
}

#: Ordinal. The order is the only thing a size axis means, so it is fixed here
#: and never re-derived by sorting labels.
SIZE_ORDER: tuple[str, ...] = (
    SIZE_ZERO,
    SIZE_1_9,
    SIZE_10_49,
    SIZE_50_249,
    SIZE_250_PLUS,
    UNKNOWN,
)


def classify_employee_size(raw) -> str:
    """The size band for a reported employee count.

    A negative count cannot be true and becomes `unknown` rather than being
    clamped to zero — clamping would turn a data fault into a plausible-looking
    measurement. A non-numeric value does the same.
    """
    if raw is None or isinstance(raw, bool):
        return UNKNOWN
    try:
        count = int(raw)
    except TypeError, ValueError:
        return UNKNOWN
    if count < 0:
        return UNKNOWN
    if count == 0:
        return SIZE_ZERO
    if count <= 9:
        return SIZE_1_9
    if count <= 49:
        return SIZE_10_49
    if count <= 249:
        return SIZE_50_249
    return SIZE_250_PLUS


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

# Estonia's fifteen counties, keyed on the roster's own upper-case spelling.
#
# The roster carries a structured county column, so nothing here parses an
# address. Address parsing was considered and rejected: it would be the one part
# of this importer that reads a free-text field capable of holding anything, and
# a structured column that is already 99.9 % populated makes it unnecessary.
COUNTY_LABELS: dict[str, str] = {
    "harjumaa": "Harjumaa",
    "hiiumaa": "Hiiumaa",
    "ida-virumaa": "Ida-Virumaa",
    "jogevamaa": "Jõgevamaa",
    "jarvamaa": "Järvamaa",
    "laanemaa": "Läänemaa",
    "laane-virumaa": "Lääne-Virumaa",
    "polvamaa": "Põlvamaa",
    "parnumaa": "Pärnumaa",
    "raplamaa": "Raplamaa",
    "saaremaa": "Saaremaa",
    "tartumaa": "Tartumaa",
    "valgamaa": "Valgamaa",
    "viljandimaa": "Viljandimaa",
    "vorumaa": "Võrumaa",
    UNKNOWN: UNKNOWN_LABEL,
}

_COUNTY_FOLD = str.maketrans({"õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z"})


def classify_region(raw) -> str:
    """The county key for a roster region value.

    Folded to ASCII before lookup so that a county written `VÕRUMAA` in one
    export and `Vorumaa` in the next is one category rather than two.
    """
    if raw is None:
        return UNKNOWN
    folded = str(raw).strip().casefold().translate(_COUNTY_FOLD)
    return folded if folded in COUNTY_LABELS else UNKNOWN


# ---------------------------------------------------------------------------
# Sector
# ---------------------------------------------------------------------------

# NACE Rev. 2 sections, which EMTAK 2008 follows division for division. Mapping
# to sections rather than divisions is what makes the chart readable: the
# roster spans 78 divisions, and 78 bars answer no question.
#
# Each entry is the inclusive division range that belongs to the section. The
# ranges are the published classification, not a judgement made here, which is
# why this mapping can be versioned and checked rather than argued about.
SECTOR_SECTIONS: tuple[tuple[str, int, int, str], ...] = (
    ("A", 1, 3, "Põllumajandus, metsamajandus ja kalapüük"),
    ("B", 5, 9, "Mäetööstus"),
    ("C", 10, 33, "Töötlev tööstus"),
    ("D", 35, 35, "Elektrienergia ja gaasivarustus"),
    ("E", 36, 39, "Veevarustus ja jäätmekäitlus"),
    ("F", 41, 43, "Ehitus"),
    ("G", 45, 47, "Hulgi- ja jaekaubandus"),
    ("H", 49, 53, "Veondus ja laondus"),
    ("I", 55, 56, "Majutus ja toitlustus"),
    ("J", 58, 63, "Info ja side"),
    ("K", 64, 66, "Finants- ja kindlustustegevus"),
    ("L", 68, 68, "Kinnisvaraalane tegevus"),
    ("M", 69, 75, "Kutse-, teadus- ja tehnikaalane tegevus"),
    ("N", 77, 82, "Haldus- ja abitegevused"),
    ("O", 84, 84, "Avalik haldus ja riigikaitse"),
    ("P", 85, 85, "Haridus"),
    ("Q", 86, 88, "Tervishoid ja sotsiaalhoolekanne"),
    ("R", 90, 93, "Kunst, meelelahutus ja vaba aeg"),
    ("S", 94, 96, "Muud teenindavad tegevused"),
    ("T", 97, 98, "Kodumajapidamiste tegevus tööandjana"),
    ("U", 99, 99, "Eksterritoriaalsete organisatsioonide tegevus"),
)

SECTOR_LABELS: dict[str, str] = {key: label for key, _low, _high, label in SECTOR_SECTIONS}
SECTOR_LABELS[UNKNOWN] = UNKNOWN_LABEL

SECTOR_ORDER: tuple[str, ...] = tuple(key for key, _low, _high, _label in SECTOR_SECTIONS) + (
    UNKNOWN,
)


def classify_sector(raw) -> str:
    """The NACE section for a reported activity code.

    The roster stores the code as a number in most rows and as text in a few, so
    the leading digits are taken from the string form either way. A code whose
    division falls in none of the published ranges becomes `unknown` rather than
    being attached to the nearest section.
    """
    if raw is None or isinstance(raw, bool):
        return UNKNOWN
    digits = "".join(character for character in str(raw).strip() if character.isdigit())
    if len(digits) < 2:
        return UNKNOWN
    division = int(digits[:2])
    for key, low, high, _label in SECTOR_SECTIONS:
        if low <= division <= high:
            return key
    return UNKNOWN


# ---------------------------------------------------------------------------
# Tenure and joining cohort
# ---------------------------------------------------------------------------

TENURE_UNDER_1 = "under_1"
TENURE_1_2 = "years_1_2"
TENURE_3_5 = "years_3_5"
TENURE_6_10 = "years_6_10"
TENURE_11_20 = "years_11_20"
TENURE_20_PLUS = "years_20_plus"

TENURE_LABELS: dict[str, str] = {
    TENURE_UNDER_1: "Alla 1 aasta",
    TENURE_1_2: "1–2 aastat",
    TENURE_3_5: "3–5 aastat",
    TENURE_6_10: "6–10 aastat",
    TENURE_11_20: "11–20 aastat",
    TENURE_20_PLUS: "Üle 20 aasta",
    UNKNOWN: UNKNOWN_LABEL,
}

TENURE_ORDER: tuple[str, ...] = (
    TENURE_UNDER_1,
    TENURE_1_2,
    TENURE_3_5,
    TENURE_6_10,
    TENURE_11_20,
    TENURE_20_PLUS,
    UNKNOWN,
)

#: Tenure at or above this many completed years is the loyalty readout the
#: composition view leads with.
LONG_TENURE_YEARS = 11


def completed_years(start: date | None, snapshot: date) -> int | None:
    """Whole years of membership at the snapshot date.

    Measured against the snapshot rather than today, so the same snapshot always
    yields the same bands however long afterwards it is read. A start date in
    the future cannot be a tenure and returns `None`.
    """
    if start is None or snapshot is None or start > snapshot:
        return None
    years = snapshot.year - start.year
    if (snapshot.month, snapshot.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


def classify_tenure(start: date | None, snapshot: date) -> str:
    """The tenure band, in completed years, with no gap between bands."""
    years = completed_years(start, snapshot)
    if years is None:
        return UNKNOWN
    if years < 1:
        return TENURE_UNDER_1
    if years <= 2:
        return TENURE_1_2
    if years <= 5:
        return TENURE_3_5
    if years <= 10:
        return TENURE_6_10
    if years <= 20:
        return TENURE_11_20
    return TENURE_20_PLUS


def classify_join_cohort(start: date | None, snapshot: date) -> str:
    """The calendar year a current member joined in.

    This answers "which joining years are represented in today's membership".
    It does **not** answer what share of any original cohort survived: the
    roster holds only members who are still here, so every cohort is seen
    through the survivors. Nothing here or downstream calls it retention.
    """
    if start is None or snapshot is None or start > snapshot:
        return UNKNOWN
    return str(start.year)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberRow:
    """One roster row, reduced to the fields composition analytics needs.

    Note what is *not* here: no name, no registry code, no address, no contact,
    no free-text comment. The importer builds this from a spreadsheet row and
    lets the row go; an absent field cannot leak, and this is the structural
    guarantee rather than a rule someone has to remember.
    """

    status: str
    legal_form: str
    employee_size: str
    region: str
    sector: str
    tenure_band: str
    join_cohort: str
    tenure_days: int | None
    is_recent_joiner: bool


@dataclass
class CompositionTally:
    """Counts per dimension per population, plus the coverage they were read at.

    Mutable while an import streams rows through it, and read once at the end.
    """

    snapshot_date: date
    rows_read: int = 0
    counts: dict[tuple[str, str], Counter] = field(default_factory=dict)
    tenure_days: list[int] = field(default_factory=list)
    unmapped: Counter = field(default_factory=Counter)

    def add(self, row: MemberRow) -> None:
        populations = [Population.ALL_CURRENT]
        if row.is_recent_joiner:
            populations.append(Population.RECENT_JOINERS)

        values = {
            Dimension.STATUS: row.status,
            Dimension.LEGAL_FORM: row.legal_form,
            Dimension.EMPLOYEE_SIZE: row.employee_size,
            Dimension.REGION: row.region,
            Dimension.SECTOR: row.sector,
            Dimension.TENURE_BAND: row.tenure_band,
            Dimension.JOIN_COHORT: row.join_cohort,
        }
        for population in populations:
            for dimension, value in values.items():
                self.counts.setdefault((population, dimension), Counter())[value] += 1

        self.rows_read += 1
        if row.tenure_days is not None:
            self.tenure_days.append(row.tenure_days)
        for dimension, value in values.items():
            if value == UNKNOWN:
                self.unmapped[dimension] += 1

    # -- read side ---------------------------------------------------------

    def total(self, population: str) -> int:
        """The denominator for a population: rows counted, not rows classified.

        Taken from the status dimension, which every row lands in — including as
        `unknown`. Summing a dimension that could drop a row would give each
        dimension its own quietly different denominator.
        """
        return sum(self.counts.get((population, Dimension.STATUS), Counter()).values())

    def category_counts(self, population: str, dimension: str) -> dict[str, int]:
        return dict(self.counts.get((population, dimension), Counter()))

    @property
    def median_tenure_days(self) -> int | None:
        """The middle tenure, or nothing when no row carried a usable start date."""
        if not self.tenure_days:
            return None
        ordered = sorted(self.tenure_days)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) // 2

    def coverage_pct(self, dimension: str) -> Decimal | None:
        """What share of rows this dimension could actually classify."""
        if not self.rows_read:
            return None
        known = self.rows_read - self.unmapped.get(dimension, 0)
        return (Decimal(known) / Decimal(self.rows_read) * 100).quantize(Decimal("0.1"))


def build_member_row(
    *,
    status,
    legal_form,
    employees,
    region,
    sector_code,
    membership_start: date | None,
    snapshot_date: date,
) -> MemberRow:
    """Classify one roster row into buckets and drop everything else.

    This is the only function an importer needs, and it takes scalars rather
    than a row object so that no caller can hand it a record carrying fields
    this module would then be responsible for not storing.
    """
    days = None
    recent = False
    if membership_start is not None and membership_start <= snapshot_date:
        days = (snapshot_date - membership_start).days
        recent = days <= RECENT_JOINER_WINDOW_DAYS

    return MemberRow(
        status=classify_status(status),
        legal_form=classify_legal_form(legal_form),
        employee_size=classify_employee_size(employees),
        region=classify_region(region),
        sector=classify_sector(sector_code),
        tenure_band=classify_tenure(membership_start, snapshot_date),
        join_cohort=classify_join_cohort(membership_start, snapshot_date),
        tenure_days=days,
        is_recent_joiner=recent,
    )


# ---------------------------------------------------------------------------
# Labels and ordering, in one place
# ---------------------------------------------------------------------------

DIMENSION_LABEL_MAPS: dict[str, dict[str, str]] = {
    Dimension.STATUS: STATUS_LABELS,
    Dimension.LEGAL_FORM: LEGAL_FORM_LABELS,
    Dimension.EMPLOYEE_SIZE: SIZE_LABELS,
    Dimension.REGION: COUNTY_LABELS,
    Dimension.SECTOR: SECTOR_LABELS,
    Dimension.TENURE_BAND: TENURE_LABELS,
    Dimension.JOIN_COHORT: {},
}

#: Dimensions whose order carries meaning, so a chart must not sort them by size.
ORDINAL_DIMENSIONS: dict[str, tuple[str, ...]] = {
    Dimension.EMPLOYEE_SIZE: SIZE_ORDER,
    Dimension.TENURE_BAND: TENURE_ORDER,
    Dimension.STATUS: STATUS_ORDER,
    Dimension.LEGAL_FORM: LEGAL_FORM_ORDER,
}


def category_label(dimension: str, key: str) -> str:
    """The name a category is drawn under.

    A joining year is its own label, which is why that dimension has no map: a
    lookup table of every year since 1925 would be a list of numbers spelled
    twice.
    """
    if dimension == Dimension.JOIN_COHORT:
        return UNKNOWN_LABEL if key == UNKNOWN else key
    return DIMENSION_LABEL_MAPS.get(dimension, {}).get(key, key)


def ordered_keys(dimension: str, keys) -> list[str]:
    """Categories in the order the dimension should be drawn in.

    An ordinal dimension keeps its own scale order. Everything else is ranked
    largest-first by the caller, because for a nominal dimension the ranking is
    most of the answer.
    """
    present = set(keys)
    if dimension in ORDINAL_DIMENSIONS:
        return [key for key in ORDINAL_DIMENSIONS[dimension] if key in present]
    if dimension == Dimension.JOIN_COHORT:
        numeric = sorted(key for key in present if key != UNKNOWN)
        return numeric + ([UNKNOWN] if UNKNOWN in present else [])
    return sorted(present)


# ---------------------------------------------------------------------------
# Growth index
# ---------------------------------------------------------------------------

# Below these counts a share is noise. A category holding three organisations
# overall, or one recent joiner, produces a ratio that swings by tens of points
# on a single membership and would rank beside a category of eight hundred as
# though the two were equally established facts.
#
# Chosen against the real distribution: the current roster has 3 400 members and
# 178 recent joiners, so a recent category of five is about 3 % of the recent
# population — small, but enough that one organisation cannot move the index by
# more than about a fifth.
MIN_OVERALL_FOR_INDEX = 20
MIN_RECENT_FOR_INDEX = 5


@dataclass(frozen=True)
class GrowthIndexRow:
    """One category's representation among recent joiners against overall.

    100 means the category holds the same share of recent joiners as of the
    membership. Above 100 it is over-represented among them, below 100
    under-represented. It is a descriptive ratio of two shares and nothing more
    — no model, no significance claim, no smoothing.
    """

    key: str
    label: str
    overall_count: int
    recent_count: int
    overall_share_pct: Decimal
    recent_share_pct: Decimal
    index: Decimal


def growth_index(
    *,
    overall: dict[str, int],
    recent: dict[str, int],
    dimension: str,
    min_overall: int = MIN_OVERALL_FOR_INDEX,
    min_recent: int = MIN_RECENT_FOR_INDEX,
) -> tuple[tuple[GrowthIndexRow, ...], tuple[str, ...]]:
    """Which categories are over- or under-represented among recent joiners.

    Returns the rows that clear the sample floors and, separately, the keys that
    were suppressed. A suppressed category is **not** returned with a zero or an
    index of 100: it is named as withheld, because "we did not measure this
    reliably" and "this category is exactly average" are different statements.
    """
    overall_total = sum(overall.values())
    recent_total = sum(recent.values())
    if not overall_total or not recent_total:
        return (), tuple(sorted(overall))

    rows: list[GrowthIndexRow] = []
    suppressed: list[str] = []
    for key in overall:
        overall_count = overall.get(key, 0)
        recent_count = recent.get(key, 0)
        if overall_count < min_overall or recent_count < min_recent:
            suppressed.append(key)
            continue
        overall_share = Decimal(overall_count) / Decimal(overall_total) * 100
        recent_share = Decimal(recent_count) / Decimal(recent_total) * 100
        rows.append(
            GrowthIndexRow(
                key=key,
                label=category_label(dimension, key),
                overall_count=overall_count,
                recent_count=recent_count,
                overall_share_pct=overall_share.quantize(Decimal("0.1")),
                recent_share_pct=recent_share.quantize(Decimal("0.1")),
                index=(recent_share / overall_share * 100).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                ),
            )
        )

    rows.sort(key=lambda row: (-row.index, row.label))
    return tuple(rows), tuple(sorted(suppressed))
