"""Two membership datasets that must never be merged.

`public` holds the count of profiles published in the Koda.ee member directory.
`internal` holds the Chamber's own board-report membership history. They come
from different sources, count different things, and are reported separately on
every surface. Nothing here joins them, and no selector treats one as a
continuation of the other.

The split into a package is deliberate: the public model is small and settled,
the internal dataset is seven models with their own vocabularies, and keeping
them in one file would have produced the repository's longest module by a wide
margin.

`composition` is a third dataset again: aggregate counts derived from the member
roster. It is not a third membership total and is never charted beside either of
the other two — it describes what kinds of organisations the membership is made
of, not how many there are.

`register` is the deliberate 2026-08 exception to this app's old "no row-level
member data anywhere" rule: the roster's rows themselves (a curated subset of
columns, no personal contacts) and the registration codes the public directory
publishes. Rows exist so the members-list page and the roster-versus-directory
comparison can — the comparison is an identity comparison between two labelled,
dated sets, and its counts are still never merged with either membership total.
"""

from .composition import (
    CompositionSnapshotImmutable,
    MembershipCompositionSnapshot,
    MembershipCompositionValue,
)
from .internal import (
    BATCH_REASON_ORDER,
    EMPLOYEE_SIZE_BANDS,
    NON_EMPLOYEE_SIZE_BANDS,
    SIZE_BAND_ORDER,
    BatchDepartureReasonKey,
    DatePrecision,
    DecisionBatchKind,
    ExtractionConfidence,
    InternalMembershipObservation,
    InternalObservationImmutable,
    InternalSourceKind,
    IssueSeverity,
    MembershipDataIssue,
    MembershipDecisionBatch,
    MembershipDecisionBatchReason,
    MembershipDecisionBatchSizeMovement,
    MembershipHistoricalSourceDocument,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MembershipNewMemberPeriod,
    MembershipNewMemberSizeDistribution,
    MembershipRemovalReason,
    MembershipSizeMovement,
    MonthlyValueStatus,
    MovementDirection,
    NewMemberPeriodScope,
    QualityStatus,
    RemovalReasonKey,
    SizeBand,
)
from .public import (
    MembershipCountObservation,
    MembershipFeedState,
    ObservationImmutable,
)
from .register import (
    MemberDirectoryEntry,
    MemberRegisterEntry,
    MemberRegisterSnapshot,
    RegisterImmutable,
)

__all__ = [
    # Aggregate composition of the member roster. No row-level identity.
    "CompositionSnapshotImmutable",
    "MembershipCompositionSnapshot",
    "MembershipCompositionValue",
    # Public Koda.ee member directory.
    "MembershipCountObservation",
    "MembershipFeedState",
    "ObservationImmutable",
    # The member register: roster rows and published directory identities.
    "MemberDirectoryEntry",
    "MemberRegisterEntry",
    "MemberRegisterSnapshot",
    "RegisterImmutable",
    # Internal board-report membership history.
    "BATCH_REASON_ORDER",
    "EMPLOYEE_SIZE_BANDS",
    "NON_EMPLOYEE_SIZE_BANDS",
    "SIZE_BAND_ORDER",
    "BatchDepartureReasonKey",
    "DatePrecision",
    "DecisionBatchKind",
    "ExtractionConfidence",
    "InternalMembershipObservation",
    "InternalObservationImmutable",
    "InternalSourceKind",
    "IssueSeverity",
    "MembershipDataIssue",
    "MembershipDecisionBatch",
    "MembershipDecisionBatchReason",
    "MembershipDecisionBatchSizeMovement",
    "MembershipHistoricalSourceDocument",
    "MembershipMetricConflict",
    "MembershipMonthlyNewMemberValue",
    "MembershipNewMemberPeriod",
    "MembershipNewMemberSizeDistribution",
    "MembershipRemovalReason",
    "MembershipSizeMovement",
    "MonthlyValueStatus",
    "MovementDirection",
    "NewMemberPeriodScope",
    "QualityStatus",
    "RemovalReasonKey",
    "SizeBand",
]
