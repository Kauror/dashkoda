"""Source states the executive overview has to draw, and no content of its own.

Every other `e2e_seed` module publishes *content*. This one publishes a
**condition**: it runs last and marks one already-published feed as having
failed its most recent check, so the browser suite meets a source that is
showing older data after a failed refresh.

That state is invisible to a seed built only from happy paths, and it is one the
main page has to get right in two places at once — the pillar keeps its figures
rather than withdrawing them, and `Andmete seis` says why they are older than
they look. A green suite that never sees it proves neither.

## Why the news feed carries it

It is the domain whose front-page contribution survives the failure intact. The
news catalogue and its GA4 reading are both already published; a failed later
check changes neither, so the Nähtavus pillar keeps every figure and only its
provenance line changes. Marking the legal workbook instead would have put the
stale badge on the domain whose deadlines drive the timeline, and a reader
comparing the two suites could not tell which of the two facts moved the page.

## Which states are seeded here, and which are not

```text
available      seeded — legal, events, news, public directory
manual         seeded — internal board report, Commerce export
stale          seeded here
not connected  the empty-database browser suite, where every pillar is
               unavailable and says so
partial        unit tests only, deliberately
```

The last one is a decision this seed inherits rather than makes.
`apps/visibility/e2e_seed.py` records that days without detail were once placed
inside a comparison window, that the comparison rule correctly refused the two
windows as a result, and that the browser suite could then never reach the
movement lists at all — the seed was arguing with the analysis instead of
exercising it. Forcing a partial website here would reverse that on the same
data. The refusal is asserted where a rule about arithmetic belongs, in
`tests/visibility/test_website_period.py` and in the dashboard's own unit tests.
"""

from __future__ import annotations

from apps.core.feeds import FeedResult


def seed_failed_refresh() -> str:
    """Mark the news feed as having failed its most recent check.

    Writes only the feed-state row. The published articles, their catalogue and
    every analytics day are left exactly as the collectors wrote them, which is
    the whole point: a failed check must not withdraw data, and a seed that
    deleted something to simulate one would be testing the wrong thing.

    Idempotent. Re-running sets the same field to the same value, so the command
    stays safe to run twice like every other builder.
    """
    from apps.news.models import NewsFeedState
    from apps.news.selectors import get_news_summary

    if not get_news_summary().has_data:
        # Nothing published means nothing to make stale. Saying so is better
        # than writing a failure state onto a source that never succeeded.
        return "Uudiste voog: avaldamata, ebaõnnestunud kontrolli ei märgitud."

    state = NewsFeedState.objects.order_by("id").first()
    if state is None:
        return "Uudiste voog: kontrolli olekut ei ole, ebaõnnestumist ei märgitud."

    state.last_result = FeedResult.FAILED
    state.save(update_fields=["last_result"])
    return "Uudiste voog: märgitud vananenuks pärast ebaõnnestunud kontrolli."


__all__ = ["seed_failed_refresh"]
