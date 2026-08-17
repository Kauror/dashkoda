"""One view of the Uudised page — everything, on one screen.

The page used to answer its questions in one scroll — an archive table with
statistics beneath it. That worked for "find me the article about excise duty"
and worked badly for "how are we doing", so on 2026-08-16 the page grew a
**focus**: `Ülevaade`, `Uudiste mõju` and `Arhiiv`, three real `GET` links a
reader chose between.

On 2026-08-17 they merged back into one. `Uudiste mõju` had already lost its
own ranking section on 2026-08-17 earlier the same round and was down to one
chart; `Arhiiv` is a self-contained section with its own controls and search.
Splitting three sections across three addresses cost more clicks than it saved
scroll, so the page is one view again — this time with the dashboard drawn
first and the exact-lookup table last, the order the three-focus page had
already settled on.

    /uudised/                     → Ülevaade      (the only view)
    /uudised/?fookus=moju         → retired, resolves to Ülevaade
    /uudised/?fookus=arhiiv       → retired, resolves to Ülevaade

`fookus=avaldamine` retired first, on 2026-08-16: its two sections were one
chart and a short list, and the overview it sat beside was itself two
sections, so the publishing material moved onto the overview whole. `moju` and
`arhiiv` followed it into `RETIRED_FOCUSES` on 2026-08-17 — each still
resolves, to the one view that now carries every section's content, because a
saved link should keep answering its question.

`fookus=uudiskirjad` retired earlier still. The newsletters are
`Otsepostitused` now, at their own address under Koduleht, and this page no
longer has a view of them — so the key is not in `FOCUSES` and resolves like
any other unknown value. `apps/news/views.py` intercepts it before that
happens and redirects, so a saved link arrives at the page that answers it
instead of silently landing on the overview.

An unreadable focus resolves to the overview rather than to a 404: a focus is a
lens on one page, and a rotted bookmark should still show the news. `FOCUSES`
keeps the single-item shape — and `focus_options`/`FocusOption` stay real —
so a section reappearing behind its own address is a matter of adding one
`Focus` back, not rebuilding the navigation.

## Why the default is not written into the URL

`fookus=ulevaade` and no parameter at all are the same view, and only one of them
should end up in somebody's address bar. The overview link therefore omits the
parameter entirely — the same rule `build_query` already applies to the archive's
default ordering.

## What each focus carries

Every focus link carries the **whole** validated page state. That mattered when
the archive's period had to survive a trip through another view and back; a
single-item navigation still builds its one link the same way, so nothing here
special-cases the count.

What is never carried is `request.GET` itself. The state is rebuilt from resolved
values by `apps/news/periods.py`, so a hand-typed parameter this page does not
understand cannot ride along into the next view.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The query parameter this navigation reads.
PARAM_FOCUS = "fookus"

FOCUS_OVERVIEW = "ulevaade"
FOCUS_IMPACT = "moju"
FOCUS_PUBLISHING = "avaldamine"
FOCUS_ARCHIVE = "arhiiv"

#: The retired newsletter focus. Kept as a name so `views.py` can recognise an
#: old link and redirect it to `Otsepostitused`; it is deliberately **not** in
#: `FOCUSES`, so it is not offered, not rendered and not reachable as a view.
LEGACY_FOCUS_NEWSLETTERS = "uudiskirjad"


@dataclass(frozen=True)
class Focus:
    """One view of the news page.

    There was a `question` here — a sentence restating what each view was for,
    rendered under the navigation. It went on 2026-08-16 along with the three
    strings that filled it. The tab labels already name the question, and a line
    repeating the tab the reader has just clicked is one they learn to skip. The
    field is gone rather than left unrendered, so nothing has to keep three
    Estonian sentences true that nothing displays.
    """

    key: str
    label: str

    @property
    def is_default(self) -> bool:
        return self.key == FOCUS_OVERVIEW


FOCUSES: tuple[Focus, ...] = (Focus(key=FOCUS_OVERVIEW, label="Ülevaade"),)

DEFAULT_FOCUS = FOCUSES[0]

_BY_KEY = {focus.key: focus for focus in FOCUSES}

#: Retired keys and the view that inherited each one's content. `avaldamine`
#: moved onto the overview whole on 2026-08-16; `moju` and `arhiiv` followed
#: on 2026-08-17, each carrying its own section onto the same page. Every
#: resolve here is exact rather than a fallback — the same pattern as the
#: shop's `vaartus`.
RETIRED_FOCUSES: dict[str, str] = {
    FOCUS_PUBLISHING: FOCUS_OVERVIEW,
    FOCUS_IMPACT: FOCUS_OVERVIEW,
    FOCUS_ARCHIVE: FOCUS_OVERVIEW,
}


def parse_focus(raw: str | None) -> Focus:
    """The view asked for, or the overview. Never raises.

    A retired key lands on the view that inherited its content; an unknown
    value is a rotted bookmark and lands on the overview.
    """
    key = (raw or "").strip()
    return _BY_KEY.get(RETIRED_FOCUSES.get(key, key), DEFAULT_FOCUS)


@dataclass(frozen=True)
class FocusOption:
    """One link in the focus navigation."""

    focus: Focus
    is_active: bool
    query: str

    @property
    def label(self) -> str:
        return self.focus.label


def focus_query(focus: Focus, state: str = "") -> str:
    """One focus as a query string, with the page's state kept.

    The default focus contributes no parameter, so `/uudised/` stays the address
    of the overview however the reader reached it.
    """
    parts = [] if focus.is_default else [f"{PARAM_FOCUS}={focus.key}"]
    if state:
        parts.append(state)
    return "&".join(parts)


def focus_options(active: Focus, *, state: str = "") -> tuple[FocusOption, ...]:
    """Every focus, each linking to itself with the rest of the state in hand."""
    return tuple(
        FocusOption(
            focus=focus,
            is_active=focus.key == active.key,
            query=focus_query(focus, state),
        )
        for focus in FOCUSES
    )


__all__ = [
    "DEFAULT_FOCUS",
    "FOCUSES",
    "FOCUS_ARCHIVE",
    "FOCUS_IMPACT",
    "FOCUS_OVERVIEW",
    "FOCUS_PUBLISHING",
    "LEGACY_FOCUS_NEWSLETTERS",
    "PARAM_FOCUS",
    "Focus",
    "FocusOption",
    "focus_options",
    "focus_query",
    "parse_focus",
]
