"""Read paths for the membership dashboard.

Reads the current observation only, and never contacts Koda.ee. There is no
"new members this year" selector, because there is no such data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.feeds import FeedSummaryMixin

from .models import MembershipCountObservation, MembershipFeedState

# How much history the overview's small trend draws. Long enough to show a
# direction, short enough that the sparkline stays legible at card width.
DEFAULT_HISTORY_DAYS = 365

# The window the overview states the member delta over. Matches the legal-work
# and events windows so every figure on the headline strip means one month.
CHANGE_WINDOW_DAYS = 30


def get_current_membership_observation() -> MembershipCountObservation | None:
    return (
        MembershipCountObservation.objects.filter(
            source__slug=settings.KODA_MEMBERS_SOURCE_SLUG, is_current=True
        )
        .select_related("source")
        .first()
    )


def get_public_membership_history(*, days: int = DEFAULT_HISTORY_DAYS):
    """Recorded totals over the window, oldest first.

    Every row is a real reading. `synchronize_membership` writes an observation
    only when the counted total differs from the one already published, so this
    is a log of changes rather than one point per daily check — consecutive
    points are never the same number, and a flat stretch is genuinely a stretch
    where nothing moved.
    """
    since = timezone.now() - timedelta(days=days)
    return tuple(
        MembershipCountObservation.objects.filter(
            source__slug=settings.KODA_MEMBERS_SOURCE_SLUG, observed_at__gte=since
        )
        .order_by("observed_at", "id")
        .values_list("observed_at", "total_members")
    )


@dataclass(frozen=True)
class MembershipChange:
    """The current public count beside the reading it replaced.

    `previous` is `None` on a first-ever observation, and the difference is then
    unknown rather than zero: nobody has said the number did not move, only that
    nothing was recorded before it.
    """

    current: MembershipCountObservation | None
    previous: MembershipCountObservation | None

    @property
    def has_change(self) -> bool:
        return self.current is not None and self.previous is not None

    @property
    def difference(self) -> int | None:
        if not self.has_change:
            return None
        return self.current.total_members - self.previous.total_members

    @property
    def direction(self) -> str:
        difference = self.difference
        if difference is None or difference == 0:
            return "flat"
        return "up" if difference > 0 else "down"

    @property
    def label(self) -> str | None:
        """The signed difference as text, so colour is never the only signal."""
        difference = self.difference
        if difference is None:
            return None
        return f"+{difference}" if difference > 0 else str(difference)

    @property
    def since(self):
        return self.previous.observed_at if self.previous else None


def get_membership_change() -> MembershipChange:
    """The published count and the one immediately before it."""
    current = get_current_membership_observation()
    previous = None
    if current is not None:
        previous = (
            MembershipCountObservation.objects.filter(
                source__slug=settings.KODA_MEMBERS_SOURCE_SLUG,
                observed_at__lt=current.observed_at,
            )
            .order_by("-observed_at", "-id")
            .first()
        )
    return MembershipChange(current=current, previous=previous)


def get_membership_change_over(*, days: int = CHANGE_WINDOW_DAYS) -> MembershipChange:
    """The published count against the last reading before the window opened.

    The baseline is the newest observation *older* than the window, which is the
    number that stood when the window began — not the oldest reading inside it,
    which would silently drop whatever movement happened on the window's first
    day.

    If no reading predates the window, `previous` is `None` and the change is
    unknown: the count may have been published for the first time last week, and
    a difference measured from that is not a month's movement.
    """
    current = get_current_membership_observation()
    if current is None:
        return MembershipChange(current=None, previous=None)
    cutoff = current.observed_at - timedelta(days=days)
    previous = (
        MembershipCountObservation.objects.filter(
            source__slug=settings.KODA_MEMBERS_SOURCE_SLUG,
            observed_at__lte=cutoff,
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    return MembershipChange(current=current, previous=previous)


@dataclass(frozen=True)
class MembershipSummary(FeedSummaryMixin):
    """Everything the dashboard needs to describe the count honestly."""

    observation: MembershipCountObservation | None
    feed_state: MembershipFeedState | None

    @property
    def has_data(self) -> bool:
        return self.observation is not None

    @property
    def total_members(self) -> int | None:
        return self.observation.total_members if self.observation else None

    @property
    def observed_at(self):
        return self.observation.observed_at if self.observation else None


def get_membership_summary() -> MembershipSummary:
    return MembershipSummary(
        observation=get_current_membership_observation(),
        feed_state=(
            MembershipFeedState.objects.filter(source__slug=settings.KODA_MEMBERS_SOURCE_SLUG)
            .select_related("source", "current_observation")
            .first()
        ),
    )
