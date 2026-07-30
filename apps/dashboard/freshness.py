"""Aggregate freshness state for the dashboard shell.

Four data modules publish through their own feed states: legal work,
membership, news and events. This module reduces those to one honest
shell-level line — how many of the wired sources currently publish data,
whether any of them is showing older data after a failed check, and when this
was computed. It reads PostgreSQL only.

What it deliberately does not do: invent an as-of date for the business data
(each module states its own), or count a source as connected merely because it
is registered. Connected means "has current published data", exactly as the
per-module summaries define it.
"""

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from apps.core.feeds import FeedResult
from apps.events.models import EventFeedState
from apps.legal_work.models import LegalWorkFeedState
from apps.membership.models import MembershipFeedState
from apps.news.models import NewsFeedState

NO_SOURCE_MESSAGE = "Andmeallikas ei ole veel ühendatud."

# The wired data modules: feed-state model, the setting naming its source slug,
# and the field pointing at its currently published data. A module joins the
# shell count by being added here, not by being guessed at.
_FEEDS = (
    (LegalWorkFeedState, "LEGAL_WORK_SOURCE_SLUG", "current_snapshot_id"),
    (MembershipFeedState, "KODA_MEMBERS_SOURCE_SLUG", "current_observation_id"),
    (NewsFeedState, "KODA_NEWS_SOURCE_SLUG", "current_snapshot_id"),
    (EventFeedState, "KODA_EVENTS_SOURCE_SLUG", "current_snapshot_id"),
)


@dataclass(frozen=True)
class FreshnessState:
    checked_at: datetime
    connected_sources: int = 0
    total_sources: int = 0
    stale_sources: int = 0

    @property
    def has_sources(self) -> bool:
        return self.connected_sources > 0

    @property
    def state_label(self) -> str:
        if not self.has_sources:
            return "Ühendamata"
        return "Vananenud" if self.stale_sources else "Ühendatud"

    @property
    def state_variant(self) -> str:
        if not self.has_sources:
            return "neutral"
        return "warning" if self.stale_sources else "success"

    @property
    def message(self) -> str:
        if not self.has_sources:
            return NO_SOURCE_MESSAGE
        base = f"Ühendatud andmeallikaid: {self.connected_sources}/{self.total_sources}."
        if self.stale_sources:
            return f"{base} Vananenud: {self.stale_sources}."
        return base


def current_freshness() -> FreshnessState:
    """Count the connected and stale sources from the recorded feed states.

    One indexed single-row query per wired module. "Stale" mirrors the
    per-module summaries: data is published but the newest check failed, so the
    dashboard is honestly showing older data.
    """
    connected = 0
    stale = 0
    for model, slug_setting, current_field in _FEEDS:
        state = (
            model.objects.filter(source__slug=getattr(settings, slug_setting))
            .values(current_field, "last_result")
            .first()
        )
        if state is None or state[current_field] is None:
            continue
        connected += 1
        if state["last_result"] == FeedResult.FAILED:
            stale += 1
    return FreshnessState(
        checked_at=timezone.localtime(),
        connected_sources=connected,
        total_sources=len(_FEEDS),
        stale_sources=stale,
    )
