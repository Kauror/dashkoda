# Website analytics (Google Analytics 4)

How DashKoda collects, stores and reads Koda.ee traffic, and what an operator
runs. For the schedule itself see [operations-runbook.md](operations-runbook.md).

## What this is

One read-only service account reads completed reporting days from the GA4 Data
API. Each day is stored as an immutable revision with its own checksum, page
rows and acquisition rows, published through the same artifact / import-run /
audit path every other source uses.

**Supported by DashKoda** and **configured in production** are different
statements and this document keeps them apart. As of 2026-08-09 the collection
is configured and scheduled in production; the historical backfill is not yet
run.

## The data model

| Model | One row is | Key |
| --- | --- | --- |
| `Ga4DailySnapshot` | one revision of one reporting day | one **current** revision per date |
| `Ga4PageDaily` | one page on one day, inside a revision | one path per revision |
| `Ga4ChannelDaily` | one acquisition channel on one day | one channel per revision |

### Why a revision per day

GA4 keeps adjusting a day for several days after it ends — late hits arrive,
sessions are attached to identities, bot traffic is reclassified. A model with
one "current reading" cannot express that, and rewriting the stored figure would
destroy what the board was shown last week.

So a day whose normalised figures have changed is republished as a **new
revision** naming the one it replaces. The replaced row keeps its figures. A
partial unique index guarantees exactly one current revision per date, which is
what stops a chart counting a Tuesday twice.

`report_date` is duplicated onto the page and channel rows deliberately: every
question — an article's first week, a quarter's top content — filters by date
across many pages, and carrying the date on the child keeps that an index range
scan rather than a join per row.

## Metrics collected

Site-wide, per day: `sessions`, `activeUsers`, `newUsers`, `screenPageViews`,
`engagedSessions`, `userEngagementDuration`.

Per page per day: `screenPageViews`, `activeUsers`, `userEngagementDuration`,
keyed by `date` × `pagePath`.

Per channel per day: `sessions`, `engagedSessions`, keyed by `date` ×
`sessionDefaultChannelGroup`.

Every name above was verified against the live property before being written
down. Engagement **rate** is not stored: it is `engaged_sessions / sessions`,
and a stored quotient is a second answer to a question two counts already
answer.

### Nothing that identifies a person

No client ID, no user ID, no IP address, no demographic or geographic breakdown.
Every stored row is an aggregate over a whole reporting day.

## The rule about users

**Sessions and page views add up. Users do not.**

Monday's 400 active users and Tuesday's 380 are not 780 people — most of them
are the same people. There is no arithmetic over daily distinct counts that
produces a period distinct count; the only honest source of "how many people in
March" is a GA4 query whose date range *is* March.

DashKoda therefore never sums `active_users`. Where a period figure is wanted it
reports the **busiest single day** and says so (`peak_active_users`,
`Kasutajaid tipppäeval`). A summable users total does not exist in the codebase,
and a test asserts that it does not, because a column that can be `SUM()`-ed
eventually is.

## Page paths

`apps/visibility/ga4_paths.py` is the only place a path is made canonical, and
both sides of every comparison go through it.

- the host is dropped — `koda.ee` and `www.koda.ee` are one site;
- **the query string is dropped** — this is the consequential one. A newsletter
  link arrives as `?utm_source=…` and a share as `?fbclid=…`; treating those as
  separate pages splits one article's readership across a dozen rows;
- the fragment is dropped, and a trailing slash, except at the root;
- case and percent-encoding are **kept**. Merging those would file one article's
  views under another, and a wrong match is invisible in the numbers.

Rows that canonicalise to the same path are folded before storage: views are
summed, `active_users` takes the larger rather than the sum.

## News analytics

`NewsItem.canonical_url` and GA4's `pagePath` meet through the canonical path.
The match is **exact** — no prefix guessing, no fuzzy title matching.

Nothing is stored on a `NewsItem`: it belongs to an immutable snapshot and is
re-imported whole on every sync, so a counter written onto it would be a mutable
figure inside a frozen record. The association is computed in bulk instead — a
list of fifty articles costs six queries, and a test asserts it.

Per article: first 7 days, first 30 days, last 30 days, and the total within
GA4's coverage. An article published **before** collection began does not print
a lifetime total; it prints what was measured and states the coverage start.

The word is `lehevaatamist`, never `lugejat`: one person opening an article
twice is two page views and one reader.

### A known limitation

The Koda.ee news feed is a rolling window of ten items, and retired news
snapshots are pruned after a week. DashKoda therefore knows the *titles* of only
the articles currently in the feed. Traffic to older articles is still stored
and still shown — as a path, in the top-content tables — but cannot be labelled
with a headline. Giving articles a durable identity (as `LegalMatter` does for
legal work) would fix this and is not part of this work.

## What may be ranked as content

Every path GA4 reports is stored, and every one of them counts towards the
site's own figures: sessions, page views, the traffic chart, the channel
breakdown. `/et` alone is 133 588 measured page views and it is in all of them.

The **content ranking** and **content search** answer a narrower question — "which
piece of content was most read" — and a language homepage is not a piece of
content. `apps/visibility/content_ranking.py` holds the one exclusion registry,
built from the real stored history rather than guessed:

| family | example | paths | views |
| --- | --- | --- | --- |
| language roots | `/et` | 4 | 172 919 |
| Drupal node aliases | `/et/node/1173` | 10 784 | 121 667 |
| cart and checkout | `/et/cart` | 17 756 | 38 928 |
| error documents | `/403.html` | 1 097 | 16 374 |
| internal search | `/et/search/node` | 7 | 13 723 |
| user and authentication | `/et/user/login` | 50 | 1 994 |
| taxonomy listings | `/et/taxonomy/term/47` | 79 | 1 124 |
| system routes | `/et/system/404` | 75 | 371 |
| uploaded assets | `/sites/default/files/…` | — | — |

Two rules govern it:

- **matching is by whole path segment.** `/en/services/search-cooperation-partner`
  is a service the Chamber sells, and a substring rule on "search" would delete
  it from every ranking with nothing looking wrong afterwards. Only the error
  documents match by prefix, because GA4 records them with the failed address
  appended;
- **under-excluding is the safer error.** `/et/pood`, `/et/astu-liikmeks`,
  `/et/parkimine` and every other ordinary page stay eligible. They belong to
  none of the three registered sections, and "how many people read this?" is
  still a fair question about them.

Excluded traffic is excluded from a *list*, never from a *total*. Nothing is
deleted and nothing is hidden.

## Finding one page

The ranking is the twenty most-read pieces of content in the chosen period. It
stays twenty: a longer list is not how somebody finds a particular page.

Search is the second mode, over the **whole measured population** —
`?otsing=…`, beside the section filter. A page ranked #347 is exactly the kind
of page somebody looks up, so searching the ranking would answer only for pages
already on screen.

It matches two things:

- **the canonical path**, however it was typed. `liikmemaks`,
  `/et/liikmed/liikmemaks` and a pasted
  `https://www.koda.ee/et/liikmed/liikmemaks` all find the same page;
- **titles DashKoda holds on authority** — the durable news catalogue and the
  public event catalogue. That is what lets "islandi" find
  `Eesti–Islandi ärifoorum`. Services have no title catalogue, and are found by
  path; nothing is ever derived from a slug, so a page DashKoda cannot name
  shows its path rather than an invented title.

A result carries two figures, and they answer different questions:

- **Valitud perioodil** — views inside the window the reader chose;
- **Kokku mõõdetud** — every view across GA4's available coverage. Not a
  lifetime: for a page older than the coverage it is "as much as was measured",
  which is why the coverage start is stated beside it.

The period and the section both apply to a search, and every control carries the
whole state — changing the window keeps the term and the section, and clearing
the search keeps the window and the section. Results are ordered by
selected-period views, 25 to a page.

Nothing here contacts Google. Search reads stored `Ga4PageDaily` rows and the
two catalogues, so the page works when Google does not.

## Reading it

The **Nähtavus** page carries the history: six periods (30 päeva, 90 päeva,
1 aasta, 3 aastat, 5 aastat, Kõik), each a real URL. A period the property
cannot fill is shown disabled rather than hidden.

The grain follows the span — daily under ~120 days, weekly under ~400, monthly
beyond — and the aggregation happens in PostgreSQL rather than by loading years
of rows into Python.

`Kust liiklus tuli` — the channel breakdown — is a `<details>` and starts shut.
The rows are rendered into the page either way, so it is a disclosure and not a
fetch; what closing it buys is the space between the traffic chart and `Enim
vaadatud sisu`, which a dozen channel rows used to push below the fold. The
count sits in the summary so a shut box still says how much is inside.

The overview card keeps its single headline figure and gains nothing.

## Operating it

### Verify configuration and coverage

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec web \
  python manage.py ga4_status
```

Reports whether GA4 is configured (never *what* with), the stored span, missing
days, page-row counts, the next reconciliation window and the last result. Makes
no Google request, so it answers the same way when the credential is broken —
which is when it gets run.

### Dry-run the ordinary sync

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec web \
  python manage.py sync_ga4 --dry-run --json
```

### The ordinary daily reconciliation

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec web \
  python manage.py sync_ga4 --json
```

Re-reads the last **eight completed days** and republishes any that changed.
Three API requests. This is what the schedule runs; see the runbook.

Today is never collected: a partial day publishes a figure that is wrong by
construction.

### Backfill a bounded range

```bash
docker compose -f compose.yaml -f compose.unraid.yaml exec web \
  python manage.py sync_ga4 --start-date 2023-06-16 --end-date 2023-12-31 --json
```

Walks the range in 31-day chunks. Start small, check `ga4_status`, then widen.

**The property's earliest data is 2023-06-16** — a read-only audit on 2026-08-09
found 1 151 days with data, so "five years" is about three years and two months
in practice. Asking for anything earlier returns days with no figures.

### Resume an interrupted backfill

Run the same command again. There is no cursor to repair: a day whose figures
are already published produces nothing, so re-reading a range that partly landed
costs API calls and writes only what is missing.

### Recovering from a failed run

A failure leaves every already-published day exactly where it is and records a
sanitized reason on the feed state, visible in the admin and in `ga4_status`.
Nothing needs undoing; diagnose, then re-run.

## Efficiency

`date` is a GA4 dimension, so one request covers a whole chunk. A five-year
backfill is roughly 3 requests × 38 chunks ≈ 115 requests, not 1 800. Pagination
follows GA4's `rowCount`/`offset` and is bounded by arithmetic rather than a
`while True`.

Retries are bounded (4 attempts, 2/8/30-second backoff) and cover only a rate
limit or a 5xx. A 400 or a 401 fails immediately: repeating either turns one
clear failure into four identical ones.

## Retention

GA4 history is **not** enrolled in `apps/sources/retention.py`. That registry is
written out by hand precisely so a new model cannot be swept into a seven-day
cleanup by accident, and daily analytics facts are long-term history. Superseded
revisions are also kept; if that ever needs a policy, it is a separate decision.

## Credentials

`GA4_PROPERTY_ID` and `GA4_CREDENTIALS_FILE` come from the environment only.
There is deliberately no `--property` or `--credentials` option, so neither can
enter shell history or a process listing.

The service account holds `analytics.readonly` and nothing wider. The key file
lives outside Git, mounted read-only into the container. No error message, log
line, audit summary, JSON payload or page ever carries the property ID, the
credential path, a token or any part of a Google response body.

## BigQuery — a recommendation, not a dependency

DashKoda uses the GA4 Data API and will continue to. It does not need BigQuery
and none is configured.

The Chamber should nevertheless consider enabling GA4 → BigQuery daily export,
separately from DashKoda, as a raw-event archive:

- the Data API returns **aggregates**. BigQuery export delivers raw events, which
  is the only way to answer questions nobody has thought of yet;
- **the export is not retroactive.** It begins producing data on the day it is
  enabled and can never fill in the past, so every month of delay is a month of
  raw history that will not exist;
- GA4's own reporting windows expire; an export outlives them;
- access must be tightly controlled — raw event data is far more identifying
  than anything DashKoda stores.

Enabling it requires a Google Cloud project and creates billing. It is a decision
for the Chamber, not something this application should do on its behalf.

## Known limitations

- coverage begins 2023-06-16; the five-year selector shows what exists;
- article titles are known only for items currently in the news feed (above);
- `file_download` is not among the events the property currently records, so
  opinion-PDF download counts are not available;
- acquisition is stored at channel-group level only — no source/medium or
  campaign breakdown yet;
- the peak-day user figure is a lower bound on period reach, by design.
