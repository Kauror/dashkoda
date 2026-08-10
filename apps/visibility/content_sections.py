"""Which part of Koda.ee a measured page belongs to.

A small validated registry rather than a prefix typed into a template. Two
reasons, and the second is the one that matters:

- a reader filtering the ranking is choosing from a closed set, so the query
  parameter can be validated against it and an unknown value falls back instead
  of reaching the database;
- **section membership is by whole path segment, never by substring.**
  `/et/uudiseks` starts with the same eight characters as `/et/uudised` and is a
  different section; filing one under the other would move real traffic to the
  wrong report, and nothing in the numbers would look wrong afterwards.

The prefixes were read out of the stored history rather than assumed. As of
2026-08-10 the property's distinct paths group as `/et/uudised` (3 201),
`/et/sundmused` (2 135) and `/et/teenused` (906), with `/en/…` and `/ru/…`
counterparts for the same content. The language variants are included in their
section: they are the same article in another language, and excluding them would
undercount an article that was translated.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ga4_paths import canonical_path

#: The query parameter the filter carries.
PARAM_CONTENT = "sisu"


@dataclass(frozen=True)
class ContentSection:
    """One filter over the ranking.

    `prefixes` is empty for "everything", which is the absence of a filter
    rather than a prefix that happens to match all paths.
    """

    key: str
    label: str
    prefixes: tuple[str, ...] = ()
    #: What one page in this section is called, for the type badge.
    item_label: str = ""

    @property
    def is_everything(self) -> bool:
        return not self.prefixes

    @property
    def index_paths(self) -> tuple[str, ...]:
        """The section's own listing pages.

        `/et/uudised` is where the articles are listed, not one of them. It
        collects the traffic of everyone browsing to an article and would sit
        permanently at the top of a ranking of the articles themselves.
        """
        return self.prefixes

    def contains(self, path: str) -> bool:
        """Whether a canonical path sits in this section.

        Whole segments only: a section root itself, or something beneath it.
        """
        if self.is_everything:
            return True
        return any(path == prefix or path.startswith(prefix + "/") for prefix in self.prefixes)


SECTION_ALL = ContentSection(key="koik", label="Kõik lehed")
SECTION_SERVICES = ContentSection(
    key="teenused",
    label="Teenused",
    prefixes=("/et/teenused", "/en/services", "/ru/uslugi"),
    item_label="Teenus",
)
SECTION_NEWS = ContentSection(
    key="uudised",
    label="Uudised",
    prefixes=("/et/uudised", "/en/news"),
    item_label="Uudis",
)
SECTION_EVENTS = ContentSection(
    key="sundmused",
    label="Sündmused",
    prefixes=("/et/sundmused", "/en/events"),
    item_label="Sündmus",
)

CONTENT_SECTIONS: tuple[ContentSection, ...] = (
    SECTION_ALL,
    SECTION_SERVICES,
    SECTION_NEWS,
    SECTION_EVENTS,
)

DEFAULT_SECTION = SECTION_ALL

_BY_KEY = {section.key: section for section in CONTENT_SECTIONS}

#: The sections a path can be *labelled* as. `Kõik lehed` is a filter, not a
#: kind of page, so it is not among them.
_CLASSIFIABLE = (SECTION_NEWS, SECTION_EVENTS, SECTION_SERVICES)


def all_index_paths() -> tuple[str, ...]:
    """Every section listing page, for the ranking to leave out."""
    return tuple(path for section in CONTENT_SECTIONS for path in section.index_paths)


def parse_section(raw: str | None) -> ContentSection:
    """The section asked for, or everything. Never raises.

    A stale bookmark or a hand-typed value resolves to `Kõik lehed`, and no
    part of the request reaches a query — the prefixes come from the registry,
    never from the parameter.
    """
    return _BY_KEY.get((raw or "").strip(), DEFAULT_SECTION)


def section_of(path: str) -> ContentSection | None:
    """Which section a path belongs to, or `None` for a page that is neither.

    Used for the type badge in the all-pages ranking. A page outside every
    known section gets no badge rather than a guessed one.
    """
    canonical = canonical_path(path)
    if not canonical:
        return None
    for section in _CLASSIFIABLE:
        if section.contains(canonical):
            return section
    return None


__all__ = [
    "CONTENT_SECTIONS",
    "DEFAULT_SECTION",
    "PARAM_CONTENT",
    "SECTION_ALL",
    "SECTION_EVENTS",
    "SECTION_NEWS",
    "SECTION_SERVICES",
    "ContentSection",
    "all_index_paths",
    "parse_section",
    "section_of",
]
