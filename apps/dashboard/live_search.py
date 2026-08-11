"""Live search: results that filter down while somebody types.

Every search box in DashKoda is a plain `GET` form that works with JavaScript
switched off. This module is what makes the same box *also* filter as you type,
without either behaviour knowing about the other:

- the form still submits, still reloads the page, still renders the same rows
  server-side. Nothing here is required for a search to work;
- with the bundle running, htmx sends the same query to a fragment endpoint on
  each keystroke and swaps **only the results region**. The input is never
  inside that region, so the caret, the selection and the focus ring survive a
  swap — which is the whole reason the region is drawn around the results
  rather than around the section.

## Why the address bar is rewritten server-side

A live search that leaves the URL behind is a search whose result cannot be
reloaded, bookmarked or sent to somebody. `hx-push-url="true"` would push the
*fragment* endpoint, so reloading would land the reader on a bare results
partial with no page around it. Instead each fragment answers with an
`HX-Push-Url` header naming the real page, and `push_url` below builds it.

## Where the rest of the page's state comes from

A keystroke only carries its own form. The Nähtavus page has two searches, a
period and a content section, and pushing a URL built from one form alone would
silently drop the other three the moment a reader reloaded.

htmx sends `HX-Current-URL` with every request, so the answer is already in
hand: take the query the reader currently has, overlay what this keystroke
changed, and push that. **Only the query string is taken from that header, and
only keys the page declares** — the path always comes from `reverse()` here.
The header is ordinary client input, and a URL echoed back into `HX-Push-Url`
is a URL the browser will put in its own address bar.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.http import QueryDict
from django.shortcuts import render

#: Longest query string this will echo back into the address bar. A pushed URL
#: is only ever as long as the parameters a page declares, so anything beyond
#: this is a hand-made request rather than a reader typing.
MAX_QUERY_LENGTH = 2000


def carried_query(request, allowed: tuple[str, ...]) -> QueryDict:
    """The reader's current query, narrowed to the keys a page understands.

    Unknown keys are dropped rather than passed through: this value is on its
    way back out as a URL, and echoing arbitrary parameters into somebody's
    address bar is not something a search box should do.
    """
    current = request.headers.get("HX-Current-URL", "")
    query = urlparse(current).query if current else ""
    if len(query) > MAX_QUERY_LENGTH:
        query = ""
    carried = QueryDict(query, mutable=True)
    for key in list(carried.keys()):
        if key not in allowed:
            del carried[key]
    return carried


def push_url(
    request, *, path: str, allowed: tuple[str, ...], updates: dict, anchor: str = ""
) -> str:
    """The URL the address bar should show after this keystroke.

    `updates` is what this search changed. An empty value removes its key
    rather than pushing `?otsi=`, because a cleared box is the unfiltered page
    and its URL should say so.

    `path` is always the caller's own `reverse()` result. Nothing from the
    request contributes to it.
    """
    params = carried_query(request, allowed)
    for key, value in updates.items():
        if value:
            params[key] = value
        else:
            params.pop(key, None)

    query = params.urlencode()
    url = f"{path}?{query}" if query else path
    return f"{url}{anchor}" if anchor else url


def search_fragment(request, template: str, context: dict, *, pushed: str):
    """Render a results partial and tell the browser what URL it represents.

    `Cache-Control: private, no-store` is not set here — every one of these
    routes is viewer-protected, and `apps.access.middleware` already stamps it
    on protected responses. An expired session is likewise the middleware's
    job: it answers an HTMX caller with `204` and `HX-Redirect`, so a search
    that outlives its session navigates to the gate instead of swapping a login
    form into the results.
    """
    response = render(request, template, context)
    response.headers["HX-Push-Url"] = pushed
    return response


__all__ = ["MAX_QUERY_LENGTH", "carried_query", "push_url", "search_fragment"]
