"""Presentation-only navigation model for the dashboard shell.

The overview and Õigusloome are routed. The remaining entries describe planned
modules so that the shell communicates the intended scope honestly; they are
rendered as disabled items marked `Lisamisel` and never as links, so no
navigation can lead to a 404 and no premature business route or placeholder app
is created.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str | None = None

    @property
    def is_available(self) -> bool:
        return self.url_name is not None


NAVIGATION: tuple[NavItem, ...] = (
    NavItem(key="overview", label="Ülevaade", url_name="home"),
    NavItem(key="membership", label="Liikmeskond"),
    NavItem(key="legislation", label="Õigusloome", url_name="legal-work"),
    NavItem(key="opinions", label="Arvamused"),
    NavItem(key="events", label="Sündmused"),
    NavItem(key="news", label="Uudised"),
    NavItem(key="finance", label="Finantsid"),
)
