"""Refuse an import that would replace the dashboard with a far smaller dataset.

A generator can stop producing most of its records without failing: a format
change upstream that every row silently fails to match still yields a valid,
well-formed workbook, just a much emptier one. That has happened here. Nothing
compared the new dataset with the one already published, so the smaller reality
was accepted and the dashboard simply showed less than it had the day before.

This is the comparison that was missing. It is deliberately one-directional:

- growth is never blocked, however large;
- a first import is never blocked, because there is nothing to compare with;
- only a collapse below `FEED_COLLAPSE_MIN_RATIO` of the published count is
  refused, and refusing means the previous snapshot stays exactly where it is.

It is a question rather than a ceiling. When a dataset genuinely shrinks -- a
department archives old years, a source is trimmed on purpose -- the operator
answers it once with `--allow-collapse` and the import proceeds.
"""

from __future__ import annotations

from django.conf import settings

DEFAULT_MIN_RATIO = 0.5


def minimum_ratio() -> float:
    """The configured floor, as a fraction of the currently published count."""
    return float(getattr(settings, "FEED_COLLAPSE_MIN_RATIO", DEFAULT_MIN_RATIO))


def collapse_reason(
    *,
    current_count: int | None,
    incoming_count: int,
    noun: str,
    allow_collapse: bool = False,
) -> str | None:
    """Return an Estonian refusal message, or ``None`` when publishing may proceed.

    `current_count` is the row count of the snapshot on the dashboard right now,
    or ``None`` when nothing is published yet. `noun` names the rows in Estonian
    partitive ("kirjet", "sündmust") so the message reads naturally.

    The caller raises its own importer error with this message, so each feed
    keeps its own error contract and its own disclosure path.
    """
    if allow_collapse:
        return None
    if current_count is None or current_count <= 0:
        return None
    if incoming_count >= current_count:
        return None

    ratio = incoming_count / current_count
    floor = minimum_ratio()
    if ratio >= floor:
        return None

    return (
        f"Import lükati tagasi: uues töövihikus on {incoming_count} {noun}, "
        f"praegu avaldatud hetktõmmises {current_count}. See on "
        f"{ratio * 100:.0f}% varasemast ja jääb alla lubatud "
        f"{floor * 100:.0f}% piiri. Varem avaldatud andmed jäid puutumata. "
        "Kui vähenemine on tegelik, käivita import võtmega --allow-collapse."
    )
