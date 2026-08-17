"""Read paths for the member register and the two-source comparison.

Reads PostgreSQL only, exactly like every other selector in this app. The two
sources it reads are the manually imported roster and the collected public
directory register, and it keeps them apart with the same discipline the rest
of the membership app applies to the two membership totals:

- **the comparison is an identity comparison, never an arithmetic one.** What
  comes out is three labelled sets — codes in both, codes only the roster has,
  codes only the directory publishes — each stated with its own source and its
  own date. Nothing here adds, subtracts, averages or reconciles the two into a
  single "true" membership number, because no such number is measured;
- **the roster is dated and stale by design.** It is a manual export; the page
  says which day it describes, and this module never presents it as current;
- **the directory is current but partial.** It publishes an identity and a
  profile link, so a code the roster does not know is shown as a link and
  nothing more. Inventing a name for it would mean scraping profile pages this
  application deliberately does not collect.

## Why the list is paginated in the database

Three and a half thousand rows is a small table and an enormous page. The list
selector applies its filters and its slice in SQL and returns at most one page
of rows, so the view never holds the whole register in memory and the response
size does not grow with the membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings
from django.db.models import Count, Q

from .composition import STATUS_LABELS, STATUS_ORDER
from .models import MemberDirectoryEntry, MemberRegisterEntry, MemberRegisterSnapshot

#: How many members one page of the list shows. Twenty is what koda.ee's own
#: directory uses, and a page of fifty rows stops being scannable.
PAGE_SIZE = 25

#: The most rows a comparison list will materialise. The mismatch sets are
#: normally a handful; a fetch fault could in principle make one enormous, and
#: a page is not the place to discover that.
MAX_COMPARISON_ROWS = 200


@dataclass(frozen=True)
class RegisterMember:
    """One row of the members list, already formatted for the template."""

    name: str
    legal_form: str
    member_number: str
    status_key: str
    status_label: str
    registry_code: str | None
    county: str
    city: str
    employees: int | None
    membership_start: date | None
    nace_label: str
    website: str
    #: Set when the public directory publishes this member's profile. The link
    #: is the directory's, not the roster's — which is why an unlisted member
    #: simply has none rather than a guessed URL.
    profile_url: str = ""
    is_published: bool = False

    @property
    def status_display(self) -> str:
        """The roster's own wording, falling back to the vocabulary label."""
        return self.status_label or STATUS_LABELS.get(self.status_key, "")

    @property
    def website_url(self) -> str:
        """A fetchable address, or nothing.

        The roster writes `www.example.ee` about as often as it writes a full
        URL. A scheme is added for the href only; nothing rewrites the stored
        value, and a row with no website gets no link rather than a dead one.
        """
        value = self.website.strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            return value
        return f"https://{value}"


@dataclass(frozen=True)
class RegisterSnapshotInfo:
    """The reading the list describes, and how big it was."""

    snapshot_date: date
    row_count: int
    imported_at: datetime


@dataclass(frozen=True)
class MemberListPage:
    """One page of members, with everything the pager needs to draw itself."""

    members: tuple[RegisterMember, ...]
    total: int
    page: int
    page_size: int
    query: str
    status: str

    @property
    def page_count(self) -> int:
        return max(1, -(-self.total // self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count

    @property
    def first_index(self) -> int:
        return 0 if not self.total else (self.page - 1) * self.page_size + 1

    @property
    def last_index(self) -> int:
        return min(self.total, self.page * self.page_size)


@dataclass(frozen=True)
class ComparisonEntry:
    """One member that appears in only one of the two sources."""

    registry_code: str
    name: str = ""
    status_label: str = ""
    profile_url: str = ""


@dataclass(frozen=True)
class SourceComparison:
    """What the roster and the public directory each know, as sets.

    Deliberately three counts and two lists rather than one reconciled number.
    A member the roster has and the directory does not is a publication
    question; a code the directory publishes and the roster does not know is
    almost always a roster that has aged since its export. Neither is an error
    in the other source, and neither licenses a correction to a membership
    total.
    """

    roster_date: date
    roster_total: int
    directory_total: int
    directory_checked_at: datetime | None
    matched: int
    only_in_roster: tuple[ComparisonEntry, ...]
    only_in_directory: tuple[ComparisonEntry, ...]
    roster_without_code: int
    truncated: bool = False
    #: How many rows each side lists before the rest are left out. Carried on
    #: the result rather than read from the module, so the note the page prints
    #: cannot drift from the number actually applied.
    limit: int = MAX_COMPARISON_ROWS

    @property
    def only_in_roster_count(self) -> int:
        return len(self.only_in_roster)

    @property
    def only_in_directory_count(self) -> int:
        return len(self.only_in_directory)

    @property
    def agrees(self) -> bool:
        return not self.only_in_roster and not self.only_in_directory


def get_current_register_snapshot() -> MemberRegisterSnapshot | None:
    return (
        MemberRegisterSnapshot.objects.filter(
            source__slug=settings.MEMBERSHIP_REGISTER_SOURCE_SLUG, is_current=True
        )
        .select_related("source")
        .first()
    )


def get_register_snapshot_info() -> RegisterSnapshotInfo | None:
    snapshot = get_current_register_snapshot()
    if snapshot is None:
        return None
    return RegisterSnapshotInfo(
        snapshot_date=snapshot.snapshot_date,
        row_count=snapshot.source_row_count,
        imported_at=snapshot.imported_at,
    )


def status_options(snapshot: MemberRegisterSnapshot | None) -> tuple[tuple[str, str, int], ...]:
    """The statuses this snapshot actually contains, in vocabulary order.

    Offering a filter for a status no member holds would be a control that can
    only ever empty the page.
    """
    if snapshot is None:
        return ()
    counts = {
        row["status_key"]: row["total"]
        for row in MemberRegisterEntry.objects.filter(snapshot=snapshot)
        .values("status_key")
        .annotate(total=Count("id"))
    }
    return tuple(
        (key, STATUS_LABELS.get(key, key), counts[key]) for key in STATUS_ORDER if key in counts
    )


def get_member_list(
    *,
    snapshot: MemberRegisterSnapshot | None = None,
    query: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> MemberListPage:
    """One page of the current roster, filtered and paginated in SQL."""
    snapshot = snapshot if snapshot is not None else get_current_register_snapshot()
    query = (query or "").strip()
    status = status if status in STATUS_LABELS else ""
    page = max(1, page)

    if snapshot is None:
        return MemberListPage(
            members=(), total=0, page=1, page_size=page_size, query=query, status=status
        )

    rows = MemberRegisterEntry.objects.filter(snapshot=snapshot)
    if status:
        rows = rows.filter(status_key=status)
    if query:
        # Name, registry code, county and sector: what a reader actually knows
        # when looking someone up. Deliberately not the member number, which is
        # an internal identifier nobody searches by.
        rows = rows.filter(
            Q(name__icontains=query)
            | Q(registry_code__icontains=query)
            | Q(county__icontains=query)
            | Q(city__icontains=query)
            | Q(nace_label__icontains=query)
        )

    total = rows.count()
    start = (page - 1) * page_size
    window = list(rows.order_by("name", "id")[start : start + page_size])

    # One extra query for the whole page rather than one per row: which of these
    # members the public directory currently publishes, and where.
    published = {
        entry.registry_code: entry
        for entry in MemberDirectoryEntry.objects.filter(
            source__slug=settings.KODA_MEMBER_DIRECTORY_SOURCE_SLUG,
            is_published=True,
            registry_code__in=[row.registry_code for row in window if row.registry_code],
        )
    }

    members = tuple(
        RegisterMember(
            name=row.name,
            legal_form=row.legal_form,
            member_number=row.member_number,
            status_key=row.status_key,
            status_label=row.status_label,
            registry_code=row.registry_code,
            county=row.county,
            city=row.city,
            employees=row.employees,
            membership_start=row.membership_start,
            nace_label=row.nace_label,
            website=row.website,
            profile_url=_profile_url(published.get(row.registry_code)),
            is_published=row.registry_code in published,
        )
        for row in window
    )
    return MemberListPage(
        members=members,
        total=total,
        page=page,
        page_size=page_size,
        query=query,
        status=status,
    )


def compare_sources(*, snapshot: MemberRegisterSnapshot | None = None) -> SourceComparison | None:
    """Which registration codes each source knows. Returns `None` if either is absent.

    Only registration codes cross the boundary between the two sources — the
    one field both of them state, and the only join this comparison needs.
    """
    snapshot = snapshot if snapshot is not None else get_current_register_snapshot()
    if snapshot is None:
        return None

    directory = MemberDirectoryEntry.objects.filter(
        source__slug=settings.KODA_MEMBER_DIRECTORY_SOURCE_SLUG, is_published=True
    )
    directory_codes = {entry.registry_code: entry for entry in directory}
    if not directory_codes:
        return None

    roster_rows = MemberRegisterEntry.objects.filter(snapshot=snapshot).values_list(
        "registry_code", "name", "status_label", "status_key"
    )
    roster_codes: dict[str, tuple[str, str]] = {}
    without_code = 0
    for code, name, status_label, status_key in roster_rows:
        if not code:
            without_code += 1
            continue
        roster_codes[code] = (name, status_label or STATUS_LABELS.get(status_key, ""))

    matched = roster_codes.keys() & directory_codes.keys()
    missing_from_directory = sorted(
        roster_codes.keys() - directory_codes.keys(), key=lambda code: roster_codes[code][0]
    )
    missing_from_roster = sorted(directory_codes.keys() - roster_codes.keys())
    truncated = (
        len(missing_from_directory) > MAX_COMPARISON_ROWS
        or len(missing_from_roster) > MAX_COMPARISON_ROWS
    )

    only_roster = tuple(
        ComparisonEntry(
            registry_code=code,
            name=roster_codes[code][0],
            status_label=roster_codes[code][1],
        )
        for code in missing_from_directory[:MAX_COMPARISON_ROWS]
    )
    only_directory = tuple(
        ComparisonEntry(
            registry_code=code,
            profile_url=_profile_url(directory_codes[code]),
        )
        for code in missing_from_roster[:MAX_COMPARISON_ROWS]
    )

    checked_at = directory.order_by("-last_seen_at").values_list("last_seen_at", flat=True).first()
    return SourceComparison(
        roster_date=snapshot.snapshot_date,
        roster_total=len(roster_codes) + without_code,
        directory_total=len(directory_codes),
        directory_checked_at=checked_at,
        matched=len(matched),
        only_in_roster=only_roster,
        only_in_directory=only_directory,
        roster_without_code=without_code,
        truncated=truncated,
    )


def _profile_url(entry: MemberDirectoryEntry | None) -> str:
    """A koda.ee profile link, assembled from configuration plus a stored path."""
    if entry is None or not entry.profile_path:
        return ""
    return f"{settings.KODA_PUBLIC_BASE_URL}{entry.profile_path}"
