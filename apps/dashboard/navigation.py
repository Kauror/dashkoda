"""Presentation-only navigation model for the dashboard shell.

Every entry here is routed today. The model still distinguishes a routed entry
from an unrouted one — an entry without a `url_name` renders as disabled text
marked `Lisamisel` rather than as a link, so navigation can never lead to a 404
and no premature business route or placeholder app is created — but nothing is
currently waiting behind that rule.

An entry may carry children. A child follows exactly the same rule as its
parent: it is a link when it has a route and inert text when it does not. No
entry nests today, but the shape is kept: it is how the sidebar would show that
a view belongs to a section without inventing a route for it.

Arvamused, Projektid, Finantsid and Fookusteemad were listed here as planned
modules and were removed at the board's request. Naming a module the sidebar
cannot open earns its place only while somebody is waiting for it; these were
reading as clutter instead.
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
    NavItem(key="legislation", label="Õigusloome", url_name="legal-work"),
    NavItem(key="events", label="Sündmused", url_name="events"),
    NavItem(key="news", label="Uudised", url_name="news"),
    # Beside Uudised: both answer "who did we reach", one by what was published
    # and one by how many people are listening.
    NavItem(key="visibility", label="Koduleht", url_name="visibility"),
    # After Nähtavus, because the shop's question builds on that one: page views
    # are the denominator of everything E-pood adds.
    NavItem(key="shop", label="E-pood", url_name="shop"),
)


def iter_items(navigation: tuple[NavItem, ...] = NAVIGATION):
    """Every entry, parents and children alike, in display order."""
    for item in navigation:
        yield item
        yield from item.children
