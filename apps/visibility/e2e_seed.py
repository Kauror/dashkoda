"""Synthetic audience figures and website analytics.

Two seeds, because the domain has two kinds of source: `seed_manual` publishes
the four social figures a staff user types, and `seed_website_analytics`
publishes GA4 days through the real collector with only its transport replaced.

The analytics seed is the one place that has to agree with several domains at
once — its paths must match what news, events and shop actually publish, or a
ranking row resolves to nothing. It therefore imports the shop's scale constants
rather than restating them.
"""

from __future__ import annotations

import datetime as dt

from apps.shop.e2e_seed import (
    SHOP_EVENT_INDEX,
    SHOP_INFORMATION_PRODUCTS,
    SHOP_MEASURED_PRODUCTS,
)

#: How much history to publish. Long enough that `30 päeva` and `90 päeva` are
#: both offered and the longer windows are visibly disabled, so the browser
#: suite sees an offered control and a refused one rather than only one branch.
ANALYTICS_DAYS = 45

#: Paths that must never reach a content ranking, with the traffic they carry.
#: They are the whole reason the ranking has an exclusion registry: on the real
#: property the language roots alone outweigh every article, so a seed without
#: them cannot show that the registry does anything. Each family in
#: `apps.visibility.content_ranking` is represented once.
ANALYTICS_UTILITY_PAGES = (
    ("/et", 900),
    ("/en", 300),
    ("/ru", 120),
    ("/et/search/node", 260),
    ("/et/cart", 180),
    ("/et/user/login", 60),
    ("/403.html", 90),
    ("/et/node/9001", 70),
)

#: A section's own listing page, which is excluded from the ranking of the
#: content it lists — otherwise every section is topped by its index.
ANALYTICS_INDEX_PAGES = (
    ("/et/uudised", 140),
    ("/et/sundmused", 130),
    ("/et/teenused", 110),
)


#: A path seeded far below the Top 20 on purpose, and the words that find it.
#: Search exists for the page a ranking cannot reach, so proving it works needs
#: a target the ranking never shows — and the term appears in no path, so only
#: the title catalogue can find it.
ANALYTICS_QUIET_PATH = "/et/uudised/sunteetiline-12"

#: How many of the seeded articles carry measured traffic. The rest are
#: catalogued and unmeasured, which is a real and common state.
MEASURED_ARTICLES = 24
ANALYTICS_QUIET_TITLE_TERM = "pealkiri 12"


def _analytics_content_pages() -> tuple[tuple[str, int], ...]:
    """The rankable paths, aligned with what the other seeders publish.

    The paths match `_seed_news` and `_seed_events` exactly, which is what lets
    a row resolve to a real title. Both halves of that are worth having:

    - **news are catalogued**, because `synchronize_news` records every item it
      publishes in `NewsResource`. So news rows show titles, `LONG_TITLE` among
      them — and it is given the heaviest traffic in the section deliberately.
      A very long linked title carrying a visually hidden suffix is the exact
      shape that widened a page by 152 pixels once, and rank one is the only
      place the layout suite will ever measure it;
    - **events and services are not.** `PublicEventResource` is filled by the
      sitemap discovery crawl, not by `synchronize_events`, and services have no
      title catalogue anywhere in the application. Their rows therefore render
      as paths — which is not a gap in the seed but the honest answer for a page
      DashKoda cannot name, and the state a real event page is in until its link
      is backfilled. Having both on screen at once is the point.
    """
    rows: list[tuple[str, int]] = []
    # Deliberately fewer than the seed publishes: the articles beyond this are
    # catalogued but unmeasured, which is what puts a real `—` in the archive's
    # view column and an unmeasured row behind the measured ones when it is
    # ranked. A seed where everything is measured cannot show either.
    for index in range(1, MEASURED_ARTICLES + 1):
        weight = 96 if index == 1 else 90 - index * 3
        # The last article is nearly silent, so it can never drift into the Top
        # 20 and quietly make the search tests assert nothing.
        rows.append((f"/et/uudised/sunteetiline-{index}", 2 if index == 12 else max(weight, 6)))
    for index in range(1, 19):
        weight = 88 if index == 1 else 80 - index * 3
        rows.append((f"/et/sundmused/sunteetiline-{index}", max(weight, 4)))
    for index in range(1, 7):
        rows.append((f"/et/teenused/sunteetiline-teenus-{index}", 70 - index * 4))
    # Shop pages, so an E-pood ranking has a denominator at all. Deliberately
    # only the first `SHOP_MEASURED_PRODUCTS` of them: the products beyond that
    # are catalogued and sold but never measured, which is what puts a real `—`
    # in the views column instead of a zero, and what a test needs in order to
    # show that unmeasured sorts behind measured.
    for index in range(1, SHOP_MEASURED_PRODUCTS + 1):
        rows.append((f"/et/pood/lepingute-naidised/sunteetiline-{index}", 64 - index * 2))
    # The informational page of the first few templates. Two pages, one product,
    # separate traffic — the split the E-pood page must never add together.
    for index in range(1, SHOP_INFORMATION_PRODUCTS + 1):
        rows.append((f"/et/tooriistad/sunteetiline-{index}", 40 - index * 3))
    # The shop's event product *is* one of the events seeded above — that is what
    # `SHOP_EVENT_INDEX` points at — so this row names a path the events loop has
    # already measured. The overlap is deliberate; emitting the path twice was
    # not, and a duplicate here is not a cosmetic problem: GA4 stores one row per
    # path per day, so the second one fails the unique constraint, the whole
    # `synchronize_ga4` call rolls back, and the seed silently produces a
    # database with no website analytics at all. Six tests were failing on it.
    rows.append((f"/et/sundmused/sunteetiline-{SHOP_EVENT_INDEX}", 22))
    rows.append(("/et/pood/tooted/sunteetiline-fyysiline", 18))
    # First weight wins. The guard is kept rather than the duplicate simply
    # deleted because this list is assembled from five independent loops over
    # four domains, and the next collision would fail exactly as quietly.
    unique: dict[str, int] = {}
    for path, weight in rows:
        unique.setdefault(path, weight)
    return tuple(unique.items())


#: Every page row, utility and content together. The site's own figures are the
#: sum of all of them, which is what lets a test show that excluding a path from
#: a *ranking* leaves the website's totals untouched.
ANALYTICS_PAGES = ANALYTICS_UTILITY_PAGES + ANALYTICS_INDEX_PAGES + _analytics_content_pages()

#: Acquisition channels and their share of a day's sessions, in whole percent.
#: The names are GA4's own default channel group, which is not Chamber data —
#: it is the vocabulary the report arrives in.
ANALYTICS_CHANNELS = (
    ("Organic Search", 42),
    ("Direct", 27),
    ("Email", 14),
    ("Organic Social", 11),
    ("Referral", 6),
)


def _analytics_day(report_date: dt.date):
    """One synthetic reporting day, shaped so the chart is not a straight line.

    The weekday rhythm is deterministic, so re-running publishes an identical
    canonical payload and the sync reports `unchanged` rather than filling the
    history with revisions of itself.
    """
    from apps.visibility.ga4 import ChannelRow, DayReading, PageRow

    # Quieter at the weekend. Integer arithmetic throughout: a float would make
    # the canonical payload depend on binary rounding, and the checksum with it.
    scale = 4 if report_date.weekday() >= 5 else 10
    pages = tuple(
        PageRow(path=path, page_views=max(base * scale // 10, 1)) for path, base in ANALYTICS_PAGES
    )

    # The site total *is* the sum of the page rows. Anything else would make
    # "excluded from the list, never from the total" untestable here.
    page_views = sum(row.page_views for row in pages)
    sessions = max(page_views // 3, 1)

    channels: list[ChannelRow] = []
    assigned = 0
    for name, share in ANALYTICS_CHANNELS[1:]:
        count = sessions * share // 100
        assigned += count
        channels.append(ChannelRow(channel=name, sessions=count))
    # The largest channel absorbs the rounding, so the parts always sum to the
    # whole and a reader can add the column up.
    channels.insert(
        0, ChannelRow(channel=ANALYTICS_CHANNELS[0][0], sessions=max(sessions - assigned, 0))
    )

    return DayReading(
        report_date=report_date,
        sessions=sessions,
        active_users=sessions * 8 // 10,
        new_users=sessions * 3 // 10,
        page_views=page_views,
        engaged_sessions=sessions * 5 // 10,
        user_engagement_seconds=sessions * 47,
        pages=pages,
        channels=tuple(channels),
        has_page_detail=True,
        has_channel_detail=True,
    ).validate()


class _SeedGa4Collector:
    """Stands in for the Data API at the seam the real collector uses.

    `synchronize_ga4` takes a collector and owns publication itself, so seeding
    substitutes the transport and nothing else: the same normalisation, the same
    canonical checksum, the same import run, the same immutable revisions. No
    request is made and no property ID or credential is read.
    """

    def collect_range(self, *, start: dt.date, end: dt.date, with_pages=True, with_channels=True):
        from apps.visibility.ga4 import CollectionCounts, RangeCollection

        days = {}
        current = start
        while current <= end:
            days[current] = _analytics_day(current)
            current += dt.timedelta(days=1)
        return RangeCollection(
            days=days,
            counts=CollectionCounts(
                requests=0,
                site_rows=len(days),
                page_rows=sum(len(day.pages) for day in days.values()),
                channel_rows=sum(len(day.channels) for day in days.values()),
            ),
        )


def seed_website_analytics(today: dt.date) -> str:
    """Publish a synthetic GA4 history so the traffic section exists at all.

    Without it `overview.html` renders the `Lisamisel` empty state and the whole
    website section — the chart, the channel table, the content ranking and the
    page search — is invisible to every browser test. Two defects shipped
    through a fully green suite that way on 2026-08-11: the view dropped the
    `otsing` parameter, and the template hid the search box behind the ranking
    it empties.
    """
    from apps.visibility.ga4_sync import synchronize_ga4

    # `synchronize_ga4` clamps to the last completed day itself; the window is
    # stated in full so the seeded span does not depend on that clamp.
    end = today - dt.timedelta(days=1)
    outcome = synchronize_ga4(
        collector=_SeedGa4Collector(),
        start=end - dt.timedelta(days=ANALYTICS_DAYS - 1),
        end=end,
        today=today,
    )
    return (
        f"veebistatistika: {outcome.result} "
        f"({ANALYTICS_DAYS} päeva, {len(ANALYTICS_PAGES)} lehekülge päevas)"
    )


def seed_manual(today: dt.date) -> str:
    from apps.visibility.manual import VisibilitySubmission, publish_submission
    from apps.visibility.registry import VisibilityMetric

    # Three readings, so every channel card has a trend and a change to state.
    # The oldest deliberately omits two metrics: a channel nobody has read yet
    # must show "andmed puuduvad" rather than a zero.
    plan = [
        (
            120,
            {
                VisibilityMetric.NEWSLETTER_ETEATAJA: 8120,
                VisibilityMetric.NEWSLETTER_ENEWS: 3040,
                VisibilityMetric.NEWSLETTER_EVESTNIK: 1210,
                VisibilityMetric.FACEBOOK_FOLLOWERS: 5210,
                VisibilityMetric.LINKEDIN_FOLLOWERS: 4130,
            },
        ),
        (
            60,
            {
                VisibilityMetric.NEWSLETTER_ETEATAJA: 8260,
                VisibilityMetric.NEWSLETTER_ENEWS: 3105,
                VisibilityMetric.NEWSLETTER_EVESTNIK: 1188,
                VisibilityMetric.FACEBOOK_FOLLOWERS: 5344,
                VisibilityMetric.LINKEDIN_FOLLOWERS: 4402,
                VisibilityMetric.INSTAGRAM_FOLLOWERS: 1870,
                VisibilityMetric.YOUTUBE_SUBSCRIBERS: 640,
            },
        ),
        (
            7,
            {
                VisibilityMetric.NEWSLETTER_ETEATAJA: 8395,
                VisibilityMetric.NEWSLETTER_ENEWS: 3162,
                VisibilityMetric.NEWSLETTER_EVESTNIK: 1174,
                VisibilityMetric.FACEBOOK_FOLLOWERS: 5498,
                VisibilityMetric.LINKEDIN_FOLLOWERS: 4655,
                VisibilityMetric.INSTAGRAM_FOLLOWERS: 1994,
                VisibilityMetric.YOUTUBE_SUBSCRIBERS: 671,
            },
        ),
    ]
    for offset, values in plan:
        publish_submission(
            VisibilitySubmission(
                observation_date=today - dt.timedelta(days=offset),
                values={str(key): value for key, value in values.items()},
                note="Sünteetiline seeme.",
            )
        )
    return f"nähtavus: {len(plan)} sisestust"
