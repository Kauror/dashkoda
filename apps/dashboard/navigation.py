"""Presentation-only navigation model for the dashboard shell.

The overview, Liikmeskond, Õigusloome, Sündmused and Uudised are routed. The
remaining entries describe planned modules so that the shell communicates the
intended scope honestly; they are rendered as disabled items marked `Lisamisel`
and never as links, so no navigation can lead to a 404 and no premature business
route or placeholder app is created.

An entry may carry children. A child follows exactly the same rule as its
parent: it is a link when it has a route and inert text when it does not.
Nesting is how the sidebar shows that Fookusteemad belongs to Õigusloome and
that Projektid has two views, without inventing a route for any of them.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str | None = None
    children: tuple[NavItem, ...] = field(default_factory=tuple)

    @property
    def is_available(self) -> bool:
        return self.url_name is not None


NAVIGATION: tuple[NavItem, ...] = (
    NavItem(key="overview", label="Ülevaade", url_name="home"),
    NavItem(key="membership", label="Liikmeskond", url_name="membership"),
    NavItem(
        key="legislation",
        label="Õigusloome",
        url_name="legal-work",
        children=(NavItem(key="focus-topics", label="Fookusteemad"),),
    ),
    NavItem(key="opinions", label="Arvamused"),
    NavItem(key="events", label="Sündmused", url_name="events"),
    NavItem(key="news", label="Uudised", url_name="news"),
    NavItem(
        key="projects",
        label="Projektid",
        children=(
            NavItem(key="projects-active", label="Käimasolevad"),
            NavItem(key="projects-finished", label="Lõppenud"),
        ),
    ),
    NavItem(key="finance", label="Finantsid"),
)


def iter_items(navigation: tuple[NavItem, ...] = NAVIGATION):
    """Every entry, parents and children alike, in display order."""
    for item in navigation:
        yield item
        yield from item.children
