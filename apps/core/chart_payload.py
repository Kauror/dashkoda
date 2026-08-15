"""The payload contract between a chart builder and the shared chart template.

Every dashboard draws its charts through one template,
`dashboard/components/chart_figure.html`, and these two dataclasses are the
shape that template renders: the drawing, the accessible table that always
accompanies it, and the analytical header of pre-formatted readouts.

Five chart modules used to each carry a byte-identical copy of both classes.
That was five definitions of one template contract — a change to what the
template renders had five places to keep honest, and they had already begun to
drift in trivia (field order, a default wording). The *content* of a chart
stays entirely domain-owned: which figures, which question, which words, which
empty-state message. This module holds only the shape those decisions travel
in, which is why it lives in `apps.core` beside `formatting` rather than in
`apps.dashboard` — a domain must be able to describe a chart without importing
the page that renders it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Readout:
    """One figure in a chart's analytical header.

    Every string arrives formatted. A template that had to decide how to write a
    signed percentage would be the second place that decision lived, and the two
    would drift the first time one of them changed.

    `direction` is the non-colour signal — a reader who cannot separate the hues
    still gets the sense of the change from the glyph beside it, and a reader
    using a screen reader gets it from `change_label`.
    """

    label: str
    value: str
    change: str = ""
    change_label: str = ""
    direction: str = ""
    note: str = ""

    @property
    def has_change(self) -> bool:
        return bool(self.change)


@dataclass(frozen=True)
class ChartPayload:
    """One chart plus the accessible alternative that always accompanies it.

    The fields beyond `option` are the analytical frame: the question the chart
    answers, the two or three figures that answer it before the reader looks at
    the drawing, and the date the drawing describes. A chart is free to use none
    of them — and a template renders only what is present.
    """

    payload_id: str
    title: str
    option: dict
    table_headers: tuple[str, ...]
    table_rows: tuple[tuple, ...]
    summary: str
    #: The line shown when there is nothing to draw. The default suits most
    #: dashboards; a domain with its own vocabulary for absence states it at
    #: the call site.
    empty_message: str = "Andmed puuduvad."
    footnotes: tuple[str, ...] = field(default_factory=tuple)
    question: str = ""
    observation_label: str = ""
    readouts: tuple[Readout, ...] = field(default_factory=tuple)
    # A design-system size name, not a pixel count: `chart_figure.html` maps it
    # to a height class. A distribution chart with four categories and a
    # five-year time series do not want the same frame, and JavaScript is not
    # needed to say so.
    size: str = "medium"

    @property
    def has_data(self) -> bool:
        return bool(self.table_rows)


__all__ = ["ChartPayload", "Readout"]
