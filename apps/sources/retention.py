"""Which retired snapshots may be deleted, and which may never be.

Twelve models publish immutable snapshots. Current ones matter; roughly a week
of retired history is enough to answer "what changed yesterday". Everything
older that nothing depends on is deletable.

**The registry is written out by hand, not discovered.** Finding snapshot models
by introspection would silently enrol the next one somebody adds, before anyone
had decided what protects it — and this module deletes things. A new family has
to be added here on purpose.

## The hazard this exists to avoid

Every match snapshot's foreign key to a source snapshot is `CASCADE`, not
`PROTECT`:

    LegalOpinionMatchSnapshot.legal_snapshot        -> LegalWorkSnapshot   CASCADE
    LegalArchivedTopicMatchSnapshot.legal_snapshot  -> LegalWorkSnapshot   CASCADE
    ...

So deleting a **retired** source snapshot that a **current** match snapshot
still pins would silently delete the current match — live matcher output, gone,
with no error. The database will not stop it. Only this policy will.

Protection is therefore **transitive**: a current archived match pins a
current-topic *match* snapshot, which in turn pins its own sources.

## What is retained

1. every current snapshot, whatever its age;
2. every snapshot inside the retention window;
3. every snapshot pinned by something already protected — which is how a retired
   source referenced by a current match survives;
4. anything a family declares for itself.

A retired snapshot pinned only by another *deletable* snapshot is **not**
protected: the pair goes together. Protection follows from what is protected,
never from what merely points.

## What this never touches

Audit events, feed-state rows, source artifacts, source files, opinion PDF
blobs, `LegalMatter` durable identities, import runs — none of them are
snapshots, and age is not a reason to delete any of them. This module only ever
deletes rows of the models named below, and their `CASCADE` children.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.apps import apps as django_apps
from django.utils import timezone

#: Approximately one week of retired history, per the retention decision.
DEFAULT_RETENTION_DAYS = 7


@dataclass(frozen=True)
class SnapshotFamily:
    """One snapshot model and everything the policy needs to know about it."""

    #: `app_label.ModelName`, resolved lazily so this module imports cleanly.
    model: str
    #: Human label for the JSON report and the audit summary.
    label: str
    #: The field that says when this snapshot came into being. **Not the same
    #: name across families**: sources stamp `imported_at` or `observed_at`,
    #: matchers stamp `generated_at`.
    cutoff_field: str
    #: Foreign keys to *other snapshots* that this one pins. Protection follows
    #: these, so a current match keeps the sources it names.
    pins: tuple[str, ...] = ()
    #: Why this family is safe to prune, in one line, for a reader who is about
    #: to run the command.
    note: str = ""


#: Every snapshot family, in dependency order: sources first, then the matchers
#: that pin them. Adding a model here is a decision to let it be deleted.
FAMILIES: tuple[SnapshotFamily, ...] = (
    SnapshotFamily(
        model="legal_work.LegalWorkSnapshot",
        label="Õigusloome hetkeseis",
        cutoff_field="imported_at",
        note=(
            "Pinned by all three matchers; a retired one survives while any "
            "current match still names it."
        ),
    ),
    SnapshotFamily(
        model="legal_work.CurrentTopicSnapshot",
        label="Hetkel käsil kataloog",
        cutoff_field="observed_at",
    ),
    SnapshotFamily(
        model="legal_work.ArchivedTopicSnapshot",
        label="Arhiivi kataloog",
        cutoff_field="observed_at",
    ),
    SnapshotFamily(
        model="legal_work.OpinionCatalogueSnapshot",
        label="Arvamuste kataloog",
        cutoff_field="observed_at",
        note="Deleting one never touches the PDF blobs it catalogued.",
    ),
    SnapshotFamily(
        model="legal_work.PublicOpinionSnapshot",
        label="Avalik arvamuskorpus",
        cutoff_field="observed_at",
        note=(
            "Deleting one never touches the PDF blobs its documents point at, "
            "and the accumulated corpus is carried whole in every snapshot."
        ),
    ),
    SnapshotFamily(
        model="news.NewsSnapshot",
        label="Uudiste hetkeseis",
        cutoff_field="imported_at",
    ),
    SnapshotFamily(
        model="events.EventSnapshot",
        label="Sündmuste kalendri hetkeseis",
        cutoff_field="imported_at",
    ),
    SnapshotFamily(
        model="event_programme.EventProgrammeSnapshot",
        label="Sündmuste programmi hetkeseis",
        cutoff_field="imported_at",
    ),
    SnapshotFamily(
        model="events.PublicEventDiscoverySnapshot",
        label="Avalike sündmuste lehtede avastusjooks",
        cutoff_field="observed_at",
        note=(
            "A run record, not a catalogue. `PublicEventResource` has no "
            "foreign key to it — the pages it found outlive every run, so "
            "deleting old runs cannot remove a single discovered link."
        ),
    ),
    SnapshotFamily(
        model="legal_work.LegalCurrentTopicMatchSnapshot",
        label="Hetkel käsil sobitamine",
        cutoff_field="generated_at",
        pins=("legal_snapshot", "current_topic_snapshot"),
    ),
    SnapshotFamily(
        model="legal_work.LegalArchivedTopicMatchSnapshot",
        label="Arhiivi sobitamine",
        cutoff_field="generated_at",
        pins=("legal_snapshot", "archived_topic_snapshot", "current_topic_match_snapshot"),
        note="Pins a current-topic match snapshot too, so protection is transitive.",
    ),
    SnapshotFamily(
        model="legal_work.LegalOpinionMatchSnapshot",
        label="Arvamuste sobitamine",
        cutoff_field="generated_at",
        pins=("legal_snapshot", "opinion_catalogue_snapshot", "public_opinion_snapshot"),
    ),
    SnapshotFamily(
        model="event_programme.EventPublicMatchSnapshot",
        label="Sündmuste viidete sobitamine",
        cutoff_field="generated_at",
        pins=("programme_snapshot",),
        note=(
            "Pins its programme snapshot by key. Its other input, the public "
            "page set, is pinned by high-water mark rather than by foreign key "
            "and is never deleted here — pruning a match cannot remove a page."
        ),
    ),
)


#: Snapshot-shaped models that are **deliberately never pruned**, and why.
#:
#: `tests/sources/test_snapshot_retention.py` finds every model whose name ends
#: in `Snapshot` and insists it appear either in `FAMILIES` or here. That is the
#: point: a new snapshot model must not be able to appear without somebody
#: deciding what happens to it. Registering one in `FAMILIES` is a decision to
#: let it be deleted; naming it here is a decision that it is history.
NEVER_PRUNED: dict[str, str] = {
    "visibility.Ga4DailySnapshot": (
        "Google Analytics reporting days are the long-term record the website "
        "history is drawn from — five years of them, one row per day. A "
        "seven-day cleanup would delete the entire chart. Superseded revisions "
        "are kept too: they are what a corrected day is corrected *from*."
    ),
}


def family_model(family: SnapshotFamily):
    return django_apps.get_model(family.model)


def retention_cutoff(*, days: int = DEFAULT_RETENTION_DAYS, now=None):
    """The moment before which a retired snapshot becomes deletable.

    Derived from `timezone.now()`, so the boundary is evaluated in application
    time like every other date in this project rather than in the container's.
    """
    return (now or timezone.now()) - timedelta(days=days)


@dataclass
class FamilyPlan:
    """What the policy decided about one family. Counts only, never content."""

    family: SnapshotFamily
    current: int = 0
    recent: int = 0
    protected: int = 0
    candidates: list = field(default_factory=list)
    oldest: object = None
    newest: object = None
    child_rows: int = 0

    @property
    def total_candidates(self) -> int:
        return len(self.candidates)

    def as_dict(self) -> dict:
        return {
            "family": self.family.model,
            "label": self.family.label,
            "current": self.current,
            "recent": self.recent,
            "protected": self.protected,
            "candidates": self.total_candidates,
            "estimated_child_rows": self.child_rows,
            "oldest_candidate": self.oldest.isoformat() if self.oldest else None,
            "newest_candidate": self.newest.isoformat() if self.newest else None,
        }


def protected_ids(cutoff) -> dict[str, set[int]]:
    """Every snapshot id that may not be deleted, per family.

    Seeded with what rule 1 and rule 2 protect — current, and recent — then
    closed transitively over `pins`. A snapshot reached only from a deletable
    one is never added, which is what lets an old source and the old match that
    names it be pruned together.
    """
    protected: dict[str, set[int]] = {family.model: set() for family in FAMILIES}
    by_model = {family.model: family for family in FAMILIES}

    frontier: list[tuple[SnapshotFamily, int]] = []
    for family in FAMILIES:
        model = family_model(family)
        seed = model.objects.filter(is_current=True).values_list("pk", flat=True)
        recent = model.objects.filter(**{f"{family.cutoff_field}__gte": cutoff}).values_list(
            "pk", flat=True
        )
        for pk in (*seed, *recent):
            if pk not in protected[family.model]:
                protected[family.model].add(pk)
                frontier.append((family, pk))

    # Close over what the protected set pins. Bounded by the number of
    # snapshots, which is small, and terminates because a pk is expanded once.
    while frontier:
        family, pk = frontier.pop()
        if not family.pins:
            continue
        model = family_model(family)
        row = model.objects.filter(pk=pk).values(*[f"{name}_id" for name in family.pins]).first()
        if row is None:
            continue
        for name in family.pins:
            target_id = row.get(f"{name}_id")
            if target_id is None:
                continue
            target_model = model._meta.get_field(name).related_model
            key = f"{target_model._meta.app_label}.{target_model.__name__}"
            if key not in by_model:
                # Pins something outside the registry: nothing to protect here,
                # and nothing this module would ever delete.
                continue
            if target_id not in protected[key]:
                protected[key].add(target_id)
                frontier.append((by_model[key], target_id))

    return protected


def plan_family(family: SnapshotFamily, *, cutoff, protected: set[int]) -> FamilyPlan:
    """Decide what is deletable in one family, without deleting anything."""
    model = family_model(family)
    stamp = family.cutoff_field

    plan = FamilyPlan(family=family)
    plan.current = model.objects.filter(is_current=True).count()
    plan.recent = model.objects.filter(**{f"{stamp}__gte": cutoff}).count()

    deletable = (
        model.objects.filter(**{f"{stamp}__lt": cutoff})
        .exclude(is_current=True)
        .exclude(pk__in=protected)
        .order_by(stamp)
    )
    plan.candidates = list(deletable.values_list("pk", flat=True))

    # How many rows a protection rule saved, as opposed to recency.
    plan.protected = (
        model.objects.filter(**{f"{stamp}__lt": cutoff})
        .exclude(is_current=True)
        .filter(pk__in=protected)
        .count()
    )

    if plan.candidates:
        stamps = list(deletable.values_list(stamp, flat=True))
        plan.oldest, plan.newest = stamps[0], stamps[-1]
        plan.child_rows = _estimated_children(model, plan.candidates)
    return plan


def _estimated_children(model, ids: list[int]) -> int:
    """How many rows would go with these snapshots, through CASCADE.

    Reported so an operator sees the real size of a prune before running it: a
    legal snapshot is one row and roughly six hundred items.
    """
    total = 0
    for relation in model._meta.related_objects:
        if relation.on_delete.__name__ != "CASCADE":
            continue
        total += relation.related_model.objects.filter(
            **{f"{relation.field.name}__in": ids}
        ).count()
    return total


def plan_retention(*, days: int = DEFAULT_RETENTION_DAYS, now=None) -> list[FamilyPlan]:
    """The whole decision, for every family, deleting nothing."""
    cutoff = retention_cutoff(days=days, now=now)
    protected = protected_ids(cutoff)
    return [
        plan_family(family, cutoff=cutoff, protected=protected[family.model]) for family in FAMILIES
    ]


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "FAMILIES",
    "NEVER_PRUNED",
    "FamilyPlan",
    "SnapshotFamily",
    "family_model",
    "plan_family",
    "plan_retention",
    "protected_ids",
    "retention_cutoff",
]
