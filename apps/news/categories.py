"""Whose news an article is: the Chamber's own, or a partner's.

Koda.ee stores this on every news node as `field_category`, a required list
field. Two of its values are the ones that mean something here:

- `meie_uudised` — the Chamber wrote it. Shown as **Koja uudised**, which is
  what the Chamber's own people call it; `Meie uudised` reads as "ours" only
  from inside Koda.ee, and DashKoda is not inside it;
- `soprade_uudised` — a partner's news the Chamber published. **Sõprade
  uudised**.

The field's other values — `arhiiv`, `artiklid`, `ajakiri_teataja`, `rss_voog` —
are the names of listing views rather than kinds of article. `arhiiv` in
particular is the whole-news listing: articles taken from `/et/uudised/arhiiv`
store `meie_uudised` or `soprade_uudised`, never `arhiiv`, which is what
establishes that the archive page is simply all news.

This module first claimed no article was stored under any of the four. That was
wrong: reading all 4 554 news nodes and translations found **one** stored as
`artiklid`. The behaviour it justified is unchanged and still right — a value
outside the two below is recorded as unknown rather than invented into a third
category — but one row is not "none", and a comment that overstates its evidence
is worse than one that states less.

**Nothing public exposes this.** The article page carries no category marker,
the RSS feed emits no `<category>`, there is no JSON:API, and the archive view
accepts no category filter — all four were checked. The two category listing
pages do expose it, and they are how newly published articles get classified;
they are capped at roughly the most recent year, which is why the history came
from a one-time authenticated read instead.
"""

from __future__ import annotations

from django.db import models


class NewsCategory(models.TextChoices):
    """The stored value is Koda.ee's own, so the two can be compared directly."""

    CHAMBER = "meie_uudised", "Koja uudised"
    PARTNER = "soprade_uudised", "Sõprade uudised"


#: The listing page each category is published on, relative to the site root.
#: These are what a public collector reads; they are complete for recent news
#: and deliberately not treated as complete for the archive.
CATEGORY_LISTINGS: dict[str, str] = {
    NewsCategory.CHAMBER: "/et/uudised/meie_uudised",
    NewsCategory.PARTNER: "/et/uudised/soprade_uudised",
}

#: Values `field_category` can hold that are listing names rather than
#: categories. Listed so an import can say "this row said `arhiiv`" instead of
#: silently dropping it.
NON_CATEGORY_VALUES = frozenset({"arhiiv", "artiklid", "ajakiri_teataja", "rss_voog"})


def parse_category(raw: str | None) -> str:
    """A stored Koda.ee value as one of ours, or an empty string.

    Empty for anything unrecognised — including the listing names — because an
    article DashKoda cannot place is not a third kind of article.
    """
    value = (raw or "").strip()
    return value if value in NewsCategory.values else ""


__all__ = ["CATEGORY_LISTINGS", "NON_CATEGORY_VALUES", "NewsCategory", "parse_category"]
