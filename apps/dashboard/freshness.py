"""Freshness state for the dashboard shell.

DashKoda has no connected data source yet, so there is nothing to report an
as-of date for. This module returns only facts about the application itself:
that no source is connected and when that was last checked. It deliberately
does not invent an as-of date, a coverage percentage or a source name.

PR-05 (`sources-audit`) added the source registry but deliberately connected
nothing to the dashboard. The first data module replaces the constant below
with real registered sources; the template contract stays the same.
"""

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

NO_SOURCE_MESSAGE = "Andmeallikas ei ole veel ühendatud."


@dataclass(frozen=True)
class FreshnessState:
    checked_at: datetime
    connected_sources: int = 0

    @property
    def has_sources(self) -> bool:
        return self.connected_sources > 0

    @property
    def state_label(self) -> str:
        return "Ühendatud" if self.has_sources else "Ühendamata"

    @property
    def state_variant(self) -> str:
        return "success" if self.has_sources else "neutral"

    @property
    def message(self) -> str:
        return NO_SOURCE_MESSAGE


def current_freshness() -> FreshnessState:
    return FreshnessState(checked_at=timezone.localtime())
