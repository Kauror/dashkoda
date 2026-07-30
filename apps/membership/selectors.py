"""Read paths for the membership dashboard.

Reads the current observation only, and never contacts Koda.ee. There is no
"new members this year" selector, because there is no such data.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from apps.core.feeds import FeedSummaryMixin

from .models import MembershipCountObservation, MembershipFeedState


def get_current_membership_observation() -> MembershipCountObservation | None:
    return (
        MembershipCountObservation.objects.filter(
            source__slug=settings.KODA_MEMBERS_SOURCE_SLUG, is_current=True
        )
        .select_related("source")
        .first()
    )


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
