"""Presentation-only navigation model for the dashboard shell.

Every entry here is routed today. The model still distinguishes a routed entry
from an unrouted one — an entry without a `url_name` renders as disabled text
marked `Lisamisel` rather than as a link, so navigation can never lead to a 404
and no premature business route or placeholder app is created — but nothing is
currently waiting behind that rule.

An entry may carry children. A child follows exactly the same rule as its
parent: it is a link when it has a route and inert text when it does not.

## The nesting is information architecture, not data architecture

`Koduleht` carries `Uudised`, `E-pood` and `Otsepostitused`. All four are what
the Chamber publishes on and around its website, and grouping them stops the
sidebar reading as seven equally weighted destinations when three of them are
facets of one.

**Sharing a menu parent joins nothing else.** Each child is a separate routed
page with its own view, its own selectors and its own semantics; `apps.news`,
`apps.shop` and the Smaily material in `apps.visibility` remain three
independent bodies of code. Nothing may be merged, cross-joined or totalled on
the strength of this tuple, which the shell reads and no selector does.

`Koduleht` itself stays clickable and opens the website dashboard: it is a page
that also has children, not a folder.

Arvamused, Projektid, Finantsid and Fookusteemad were listed here as planned
modules and were removed at the board's request. Naming a module the sidebar
cannot open earns its place only while somebody is waiting for it; these were
reading as clutter instead.

`Admin` is deliberately **not** here. It is a maintainer's destination rather
than one of the Chamber's subjects, and it is reached from the low-emphasis
foot of the sidebar beside the build stamp — see
`dashboard/partials/sidebar.html`.
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
    NavItem(
        key="visibility",
        label="Koduleht",
        url_name="visibility",
        children=(
            # What was published, what was sold and what was sent. Three
            # questions about the same public surface, and three separate pages.
            NavItem(key="news", label="Uudised", url_name="news"),
            NavItem(key="shop", label="E-pood", url_name="shop"),
            NavItem(key="mailings", label="Otsepostitused", url_name="mailings"),
        ),
    ),
)


def iter_items(navigation: tuple[NavItem, ...] = NAVIGATION):
    """Every entry, parents and children alike, in display order."""
    for item in navigation:
        yield item
        yield from item.children


def parent_key(child_key: str, navigation: tuple[NavItem, ...] = NAVIGATION) -> str:
    """Which entry owns `child_key`, or empty for a top-level or unknown key.

    The shell uses this to mark a parent while one of its children is on
    screen. That marking is deliberately not `aria-current="page"` — exactly one
    page is current, and claiming two would tell a screen-reader user they are
    in two places — so it is a quieter visual state and nothing more.
    """
    for item in navigation:
        if any(child.key == child_key for child in item.children):
            return item.key
    return ""
