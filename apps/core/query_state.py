"""Reading the query string a reader arrived with.

Four pages carry their state in the URL — a period, a search, an ordering, a
page number — so that a view can be reloaded, bookmarked and sent to somebody.
The reading of those parameters is the same problem every time, and it has one
governing rule:

**a rotted bookmark renders a page, never an error.** A hand-typed URL, a
reversed range, `lk=abc`, `kategooria=xyz` and a half-filled pair of date fields
each resolve to something queryable. Nothing here raises.

That rule is why these four live together rather than being written out per
page: when it is got wrong the failure is a 500 on a link somebody saved months
ago, and a fix should not have to be found in three places. The values also go
back out into an address bar, so what is read here bounds what is echoed there
— see `apps.dashboard.live_search` for the other half of that.

## What deliberately does **not** live here

`build_query` and `period_options` stay with their pages, and a future cleanup
should leave them there. They look alike and are not: each assembles a different
set of controls, in its own order, with its own rule for which parameter is
omitted at its default — News drops `sort` when it is the newest-first default,
E-pood drops it when empty and repeats `kategooria` once per selected category.
A shared version would have to be generic over parameter names, ordering and
omission, which is a harder thing to read than the two obvious functions it
would replace.

The period **vocabularies** stay with their pages too, and for a stronger
reason: `apps/news/periods.py` means a publication window, `apps/shop/periods.py`
means a window anchored to a frozen export, and `apps/visibility/traffic_page.py`
means a measurement window. Merging those would make three different questions
look like one. Only the mechanics are shared.
"""

from __future__ import annotations

from datetime import date


def parse_iso_date(raw: str | None) -> date | None:
    """The date a `type="date"` field submitted, or `None` for anything else.

    A date input submits `YYYY-MM-DD` and nothing else, so that is the only
    shape read. An empty field, a hand-typed URL or an injection attempt is not
    a date and resolves to "no date given" rather than to an error page.
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError, AttributeError:
        return None


def parse_page(raw: str | int | None) -> int:
    """The page number asked for, floored at one.

    A rotted bookmark is not an error page. A page beyond the end is clamped
    later, once the row count is known.
    """
    try:
        return max(int(raw), 1)
    except TypeError, ValueError:
        return 1


def parse_search(raw: str | None, *, limit: int) -> str:
    """The search term, trimmed and bounded to ``limit``.

    Free text, unlike a period or a section, which are validated against closed
    registries. What bounds it instead is that cap and the ORM: the term is only
    ever a parameter to `icontains`, never a regex and never a fragment of a
    query. The caller states the cap, because how much of a term is worth
    keeping is a question about that page's index, not a shared constant.
    """
    return (raw or "").strip()[:limit]


def parse_sort(raw: str | None, *, allowed, default: str) -> str:
    """The ordering asked for if the page offers it, otherwise its default.

    Never raises: an ordering a page does not have is not an error, it is the
    default ordering.
    """
    value = (raw or "").strip()
    return value if value in allowed else default


def parse_int_list(values) -> tuple[int, ...]:
    """Whatever of a repeated query parameter is actually an integer.

    A rotted bookmark carrying `kategooria=abc` narrows to nothing rather than
    raising; the page still renders and the reader can pick again.
    """
    out: list[int] = []
    for value in values or ():
        try:
            out.append(int(value))
        except TypeError, ValueError:
            continue
    return tuple(dict.fromkeys(out))
