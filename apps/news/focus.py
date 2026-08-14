"""Which of the five Uudised views a reader is looking at.

The page answers five different questions and used to answer them in one
scroll — an archive table with newsletter statistics beneath it. That works for
"find me the article about excise duty" and works badly for "how are we doing",
because the answer to the second was somewhere in the middle of the first.

So the page carries a **focus**: five ordinary `GET` links, each a real URL that
can be bookmarked, shared and reached with the browser's back button. No SPA, no
tab state in JavaScript, no fragment that has to be re-fetched.

    /uudised/                     → Ülevaade      (the default)
    /uudised/?fookus=moju         → Uudiste mõju
    /uudised/?fookus=avaldamine   → Avaldamine
    /uudised/?fookus=uudiskirjad  → Uudiskirjad
    /uudised/?fookus=arhiiv       → Arhiiv

An unreadable focus resolves to the overview rather than to a 404: a focus is a
lens on one page, and a rotted bookmark should still show the news.

## Why the default is not written into the URL

`fookus=ulevaade` and no parameter at all are the same view, and only one of them
should end up in somebody's address bar. The overview link therefore omits the
parameter entirely — the same rule `build_query` already applies to the archive's
default ordering.

## What each focus carries

Every focus link carries the **whole** validated page state. The archive's period
and the newsletter the reader chose survive a trip through the publishing view
and are still in force on the way back, which is what makes the five links a
navigation rather than five separate pages that forget each other.

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
FOCUS_NEWSLETTERS = "uudiskirjad"
FOCUS_ARCHIVE = "arhiiv"


@dataclass(frozen=True)
class Focus:
    """One view of the news page.

    `question` is what the view is for, in the words a reader would use. It is
    rendered under the heading of every focus except the overview, whose four
    headline figures already say what it is.
    """

    key: str
    label: str
    question: str = ""

    @property
    def is_default(self) -> bool:
        return self.key == FOCUS_OVERVIEW


FOCUSES: tuple[Focus, ...] = (
    Focus(key=FOCUS_OVERVIEW, label="Ülevaade"),
    Focus(
        key=FOCUS_IMPACT,
        label="Uudiste mõju",
        question="Mida loetakse, mis algas tugevalt ja mis püsib loetavana.",
    ),
    Focus(
        key=FOCUS_PUBLISHING,
        label="Avaldamine",
        question="Kui palju ja mida me avaldame.",
    ),
    Focus(
        key=FOCUS_NEWSLETTERS,
        label="Uudiskirjad",
        question="Kui suured on uudiskirjade nimekirjad ja kuidas saadetisi loetakse.",
    ),
    Focus(
        key=FOCUS_ARCHIVE,
        label="Arhiiv",
        question="Leia üksik uudis.",
    ),
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
    "FOCUS_NEWSLETTERS",
    "FOCUS_OVERVIEW",
    "FOCUS_PUBLISHING",
    "PARAM_FOCUS",
    "Focus",
    "FocusOption",
    "focus_options",
    "focus_query",
    "parse_focus",
]
