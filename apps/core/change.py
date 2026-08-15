"""How every dashboard states that a measure moved.

One measure, two windows, the difference, and a non-colour signal for the
direction. Nothing here interprets: a `ChangeRow` is arithmetic that has been
spelled, and no field of it may hold a sentence claiming a cause.

It lived in `apps/news/page.py` while Uudised was the only page building one.
The Otsepostitused page needs the same three primitives and is owned by
`apps.visibility`, so keeping them there would have meant either a
visibility→news import — backwards, since `apps.news` is the consumer of
Smaily and not its owner — or a second copy of a dataclass and two formatters.
This is the same consolidation `apps/core/chart_payload.py` records for the
chart payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core.formatting import percent


@dataclass(frozen=True)
class ChangeRow:
    """One line of a `Mis muutus?` list.

    Deterministic and arithmetic: a measure, its two windows and the difference.
    Nothing here is generated prose and nothing interprets.
    """

    label: str
    current: str
    previous: str
    change: str
    direction: str
    note: str = ""


def share_percent(fraction: float | None, *, places: int = 1) -> str:
    """A fraction as a percentage. `0.097` → `9,7%`."""
    if fraction is None:
        return ""
    return percent(fraction * 100, places=places)


def direction_of(value: float | int | None) -> str:
    """The non-colour signal beside a change.

    A change distinguished only by hue does not exist for a reader who cannot
    separate the hues, so every change carries a glyph and a spoken label as
    well. `flat` is a real answer and is not dressed as either direction.
    """
    if value is None:
        return ""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


__all__ = ["ChangeRow", "direction_of", "share_percent"]
