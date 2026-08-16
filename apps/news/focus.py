"""Which of the four Uudised views a reader is looking at.

The page answers four different questions and used to answer them in one
scroll — an archive table with statistics beneath it. That works for "find me
the article about excise duty" and works badly for "how are we doing", because
the answer to the second was somewhere in the middle of the first.

So the page carries a **focus**: four ordinary `GET` links, each a real URL that
can be bookmarked, shared and reached with the browser's back button. No SPA, no
tab state in JavaScript, no fragment that has to be re-fetched.

    /uudised/                     → Ülevaade      (the default)
    /uudised/?fookus=moju         → Uudiste mõju
    /uudised/?fookus=avaldamine   → Avaldamine
    /uudised/?fookus=arhiiv       → Arhiiv

`fookus=uudiskirjad` was the fifth. The newsletters are `Otsepostitused` now,
at their own address under Koduleht, and this page no longer has a view of
them — so the key is not in `FOCUSES` and resolves like any other unknown
value. `apps/news/views.py` intercepts it before that happens and redirects,
so a saved link arrives at the page that answers it instead of silently
landing on the overview.

An unreadable focus resolves to the overview rather than to a 404: a focus is a
lens on one page, and a rotted bookmark should still show the news.

## Why the default is not written into the URL

`fookus=ulevaade` and no parameter at all are the same view, and only one of them
should end up in somebody's address bar. The overview link therefore omits the
parameter entirely — the same rule `build_query` already applies to the archive's
default ordering.

## What each focus carries

Every focus link carries the **whole** validated page state. The archive's period
survives a trip through the publishing view and is still in force on the way
back, which is what makes the four links a navigation rather than four separate
pages that forget each other.

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


FOCUSES: tuple[Focus, ...] = (
    Focus(key=FOCUS_OVERVIEW, label="Ülevaade"),
    Focus(key=FOCUS_IMPACT, label="Uudiste mõju"),
    Focus(key=FOCUS_PUBLISHING, label="Avaldamine"),
    Focus(key=FOCUS_ARCHIVE, label="Arhiiv"),
)

DEFAULT_FOCUS = FOCUSES[0]

_BY_KEY = {focus.key: focus for focus in FOCUSES}


def parse_focus(raw: str | None) -> Focus:
    """The view asked for, or the overview. Never raises."""
    return _BY_KEY.get((raw or "").strip(), DEFAULT_FOCUS)


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
