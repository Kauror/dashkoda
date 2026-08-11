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
"""

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

__all__ = [
    # Public Koda.ee member directory.
    "MembershipCountObservation",
    "MembershipFeedState",
    "ObservationImmutable",
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
