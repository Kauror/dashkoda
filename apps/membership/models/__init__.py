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
    EMPLOYEE_SIZE_BANDS,
    SIZE_BAND_ORDER,
    DatePrecision,
    ExtractionConfidence,
    InternalMembershipObservation,
    InternalObservationImmutable,
    InternalSourceKind,
    IssueSeverity,
    MembershipDataIssue,
    MembershipHistoricalSourceDocument,
    MembershipMetricConflict,
    MembershipMonthlyNewMemberValue,
    MembershipRemovalReason,
    MembershipSizeMovement,
    MonthlyValueStatus,
    MovementDirection,
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
    "EMPLOYEE_SIZE_BANDS",
    "SIZE_BAND_ORDER",
    "DatePrecision",
    "ExtractionConfidence",
    "InternalMembershipObservation",
    "InternalObservationImmutable",
    "InternalSourceKind",
    "IssueSeverity",
    "MembershipDataIssue",
    "MembershipHistoricalSourceDocument",
    "MembershipMetricConflict",
    "MembershipMonthlyNewMemberValue",
    "MembershipRemovalReason",
    "MembershipSizeMovement",
    "MonthlyValueStatus",
    "MovementDirection",
    "QualityStatus",
    "RemovalReasonKey",
    "SizeBand",
]
