"""Presentation-only navigation model for the dashboard shell.

The overview, Liikmeskond, Õigusloome, Sündmused and Uudised are routed. The
remaining entries describe planned modules so that the shell communicates the
intended scope honestly; they are rendered as disabled items marked `Lisamisel`
and never as links, so no navigation can lead to a 404 and no premature business
route or placeholder app is created.

An entry may carry children. A child follows exactly the same rule as its
parent: it is a link when it has a route and inert text when it does not.
Nesting is how the sidebar shows that Fookusteemad belongs to Õigusloome,
without inventing a route for it.

Arvamused, Projektid and Finantsid were listed here as planned modules and were
removed at the board's request. Naming a module the sidebar cannot open earns
its place only while somebody is waiting for it; these three were reading as
clutter instead.
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
    NavItem(key="events", label="Sündmused", url_name="events"),
    NavItem(key="news", label="Uudised", url_name="news"),
    # Beside Uudised: both answer "who did we reach", one by what was published
    # and one by how many people are listening.
    NavItem(key="visibility", label="Nähtavus", url_name="visibility"),
)


def iter_items(navigation: tuple[NavItem, ...] = NAVIGATION):
    """Every entry, parents and children alike, in display order."""
    for item in navigation:
        yield item
        yield from item.children
