"""Which membership question the page is answering.

The Liikmeskond page grew from one report into five analytical views, and a
single scroll through all of them buries the few figures most readers open the
page for. `fookus` names which view is drawn.

It is an ordinary GET parameter and every control is a link, so a focus is
bookmarkable, shareable and reachable with the back button. There is no client
state and no SPA: the server decides what is on the page, which is the same rule
the range control and the chart toggles already follow.

Three decisions are worth stating:

- **`fookus` is a new key, deliberately.** `vaade` already means monthly versus
  cumulative inside the recruitment chart, and reusing it would make one word
  govern two unrelated things — a reader who bookmarked a cumulative chart would
  find the bookmark changing which page section existed.
- **an unknown focus is not an error.** A stale bookmark, a typo or a truncated
  URL renders the overview, which is the same rule `ranges.py` applies to a
  malformed date. A 404 for a mistyped query value would be a page that punishes
  a reader for a link somebody else wrote.
- **a focus is offered only when it has something to draw.** Composition data
  arrives through an import that may not have run, and a navigation item leading
  to an empty page reads as a fault. The keys stay stable either way, so a URL
  that works today keeps working when the import lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

#: The query key. Not `vaade` — that one already governs the recruitment chart.
PARAM_FOCUS = "fookus"

FOCUS_OVERVIEW = "ulevaade"
FOCUS_GROWTH = "kasv"
FOCUS_COMPOSITION = "koosseis"
FOCUS_FEES = "liikmemaks"
FOCUS_MOVEMENT = "liikumine"

#: Every focus, in the order the navigation lists them, with the label shown.
#:
#: The order is the reading order of the dashboard rather than an alphabet:
#: how many members there are, whether that number is growing, who they are,
#: whether they pay, and who arrived or left.
FOCUS_LABELS: tuple[tuple[str, str], ...] = (
    (FOCUS_OVERVIEW, "Ülevaade"),
    (FOCUS_GROWTH, "Kasv ja püsimine"),
    (FOCUS_COMPOSITION, "Koosseis"),
    (FOCUS_FEES, "Liikmemaks"),
    (FOCUS_MOVEMENT, "Liikumine ja põhjused"),
)

FOCUS_KEYS: tuple[str, ...] = tuple(key for key, _label in FOCUS_LABELS)

#: What an unreadable or absent `fookus` resolves to.
DEFAULT_FOCUS = FOCUS_OVERVIEW


def resolve_focus(raw: str | None) -> str:
    """The focus to draw. An unknown value is the overview, never an error."""
    return raw if raw in FOCUS_KEYS else DEFAULT_FOCUS


@dataclass(frozen=True)
class FocusLink:
    """One item in the focus navigation, as a link the server already built."""

    key: str
    label: str
    query: str
    is_active: bool


def focus_links(
    active: str,
    *,
    carried: dict[str, str] | None = None,
    available: frozenset[str] | None = None,
) -> tuple[FocusLink, ...]:
    """The navigation, carrying the window forward and nothing else.

    `carried` holds only values the view has already resolved — in practice the
    two dates of a clamped window. Copying the whole incoming query string would
    reflect a stale toggle, a typo or a hostile string back into an href, and it
    would also carry a recruitment-chart choice into a page that has no
    recruitment chart.

    The chart toggles are deliberately *not* carried: each focus resolves its own
    controls from their defaults, so switching focus cannot land a reader on a
    control state that means nothing where they arrived.

    `available` names the focuses that have something to draw; `None` offers them
    all. The active focus is always listed even when it is not available, because
    a navigation that hides the item a reader is standing on reads as a fault.
    """
    carried = dict(carried or {})
    links = []
    for key, label in FOCUS_LABELS:
        if available is not None and key not in available and key != active:
            continue
        params = dict(carried)
        params[PARAM_FOCUS] = key
        links.append(
            FocusLink(
                key=key,
                label=label,
                query=f"?{urlencode(params)}",
                is_active=key == active,
            )
        )
    return tuple(links)
