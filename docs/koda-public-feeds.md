# Public Koda.ee feeds

Three public, anonymous, read-only sources from the Chamber's own website: the
size of the member directory, the news feed, and the events calendar. They are
collected once each morning by a scheduled command and published as immutable
records, exactly like the legal-work workbook.

```text
public koda.ee endpoint
  → scheduled outbound HTTPS fetch
  → validate and normalise
  → deterministic canonical JSON
  → SHA-256 over that JSON
  → metadata-only SourceArtifact
  → existing ImportRun lifecycle
  → immutable observation / snapshot
  → PostgreSQL
  → dashboard
```

No credential exists for any of them. **No raw response is ever retained.**

## The three sources

| Source | Endpoint | Slug | Reference |
| --- | --- | --- | --- |
| member count | `/api/v1/company-list` | `koda-public-members` | `koda-public:company-list` |
| news | `/et/news/feed.xml` | `koda-public-news` | `koda-public:news-feed` |
| events | `/et/sundmused` | `koda-public-events` | `koda-public:events` |

Deliberately **not** used: the homepage membership counter, `#yearly`,
`#overall`, `/et/uudised/rss.xml` (a second, malformed RSS document), any events
RSS feed, the shop page, Drupal administration and any authenticated endpoint.

## The events calendar is a supplementary source

The dashboard's event figures and its event history come from the canonical Excel
programme in `apps.event_programme` — see
[event-programme-feed.md](event-programme-feed.md). This calendar feed is
supplementary and answers a narrower question: what has the Chamber announced
publicly and not yet held.

It therefore no longer produces:

- a historical count, a current-year total, a tag, month or type total, or a
  past-event total;
- an event identity the dashboard reads as canonical;
- a public link for a programme event.

It keeps collecting on its own schedule and keeps its own immutable snapshots.
`apps.events` has no route: `/sundmused/` is the programme's page, and this feed
is named there as a secondary connection with its own state and its own count of
publicly announced upcoming events. The two are never added, never merged and
never presented as one unlabelled total.

`count_started_in_past_window` was removed with the page that used it. It
reconstructed a past-event count by scanning every archived snapshot, because the
collector drops an event once it has finished; the workbook retains what actually
happened and answers that directly.

## What "Liikmeid kokku" means

**The number of member profiles published in the public Koda.ee member
directory at the moment of collection.** That is the whole claim.

It is **not** a count of paid members, invoiced members, accounting membership
or active CRM contracts. No public source establishes those definitions, so
DashKoda does not assert them. If an internal source later defines membership
for billing or accounting, that is a different metric with a different name.

The endpoint returns one object per published profile, carrying a registration
code (`crn`) and a profile URL. Both are used **in memory only** — the code to
detect duplicates, the URL to confirm the row is a member profile on koda.ee —
and neither is stored, logged, returned or audited. What survives collection is
a single integer.

A row counts when it is an object with a non-empty registration code and a
member profile URL on koda.ee. That test is structural on purpose: Koda's own
publication decides who belongs in the directory, and second-guessing it would
silently produce a different number.

### Explicitly excluded

**"Uusi liikmeid sel aastal" is not a DashKoda metric.** It exists in no model,
no field, no selector, no template, no JSON output and no test. The dashboard
does not show it and does not reserve an empty card for it.

**Teataja is out of scope.** No issue, no PDF, no link.

Also absent: member names, registration codes, profile URLs, individual member
records, and any per-member view.

### The change guard

A published directory does not lose or gain a large fraction of its members
overnight, so an implausible movement is treated as a source or parsing fault
rather than as news.

- the first observation always publishes — there is nothing to compare against;
- afterwards, a change is refused only when it exceeds **both**
  `KODA_MEMBERS_MAX_CHANGE_RATIO` (15%) and
  `KODA_MEMBERS_MAX_CHANGE_ABSOLUTE` (200 members).

Both must be exceeded: the absolute floor stops a small directory tripping the
proportional rule, and the proportional rule stops a large one tripping the
floor. A refused change **fails closed** — the previous observation stays
published and the check is recorded as failed. No member count is hard-coded
anywhere.

## News

The canonical `/et/news/feed.xml` is the only feed used. The site also serves a
second, malformed RSS document; silently falling back to it would mean the
dashboard sometimes showed a different, lower-quality dataset without saying so.
When the feed is unavailable the run fails and the previous snapshot stays
published.

Retained per item: GUID, title, canonical URL, publication timestamp, category
when supplied, and a sanitized plain-text summary. Rejected outright: a missing
title, a missing GUID *and* link, a duplicate GUID or URL, an off-domain or
non-HTTPS link, a missing or unparsable publication time, and a timestamp more
than `KODA_NEWS_MAX_FUTURE_DAYS` (2) ahead — that last one under a single
documented rule, so a mis-dated item cannot pin itself to the top forever.

Summaries are reduced to plain text: scripts, styles and all markup removed,
entities resolved, whitespace collapsed, truncated at
`KODA_SUMMARY_MAX_LENGTH` (400). **No article HTML is stored.** Ordering is
newest first with the GUID as tie-break, so it never drifts between runs.

**The feed currently emits no `<category>` element.** The field exists and is
populated if one ever appears, but at present the dashboard cannot distinguish
"Meie uudised" from other categories, and it does not pretend to. The archive
therefore shows no category at all: a badge on the ten articles the feed
currently lists, against a thousand catalogued ones without, would read as a
difference between the articles rather than a difference in what DashKoda knows.

### What the feed is, and what it is not

The feed **discovers** news. It is not the archive, and `/uudised/` no longer
treats it as one.

`NewsSnapshot` is a rolling window of ten items, replaced whole on every sync
and pruned after a week. Filtering it by "the last year" cannot work however the
filter is written — the rows are simply not there. So the page reads
`NewsResource`, the durable catalogue: one row per public article, written the
first time DashKoda sees it and never deleted, outliving every snapshot that
mentioned it.

Nothing about collection changed. The feed still discovers new articles,
corrects titles and publication dates, drives source freshness and populates the
catalogue; `NewsFeedState`, the import history and the source's health are
untouched. What changed is which population the viewer reads.

### Where a publication date comes from

Two sources, and the feed outranks the page:

- an article catalogued **from the feed** carries the date the Chamber
  published. That is authoritative and is never overwritten;
- an article recovered **from its public page** is dated from schema.org
  `datePublished` in the page's own JSON-LD — a timezone-aware ISO 8601
  timestamp, present back to at least 2017.

This is a correction. `discover_news_titles` used to assert that "the page does
not reliably carry a publication date" and catalogued every recovered article
undated on that basis. It was never re-checked against the pages: forty of forty
sampled carried one. The cost was the entire archive — 3 602 of 3 614 rows
undated, so the publication-period filters had twelve articles to work on.
`backfill_news_dates` is the one-time pass that repairs it, and
`parse_published_at` dates newly discovered articles as they are found.

Three shapes are still refused rather than guessed at: a listing page such as
`/en/news`, which carries no `datePublished` and must not be dated from whatever
it happens to list; an unparseable value; and a moment more than
`KODA_NEWS_MAX_FUTURE_DAYS` ahead, the same guard the feed applies so a
mis-dated article cannot pin itself to the top forever.

An article that genuinely states no date stays undated, and an undated article
cannot answer "was this published in March" either way — so it appears only
under `Kõik`, where the claim is "everything catalogued", never "everything the
Chamber has ever published".

## Events

The listing carries no `<time>` element, no `itemprop` and no Event JSON-LD.
The **detail pages do** carry schema.org `Event` JSON-LD, so that is the
authoritative source, with a class-scoped `event--default--date` as the
documented fallback.

Two traps, both found against the live site:

- **Category pages share the event URL prefix.** `/et/sundmused/koolitused` and
  `/et/sundmused/liikmeuritused` are category listings, not events. Extraction is
  scoped to `event--teaser` cards, and an entry is kept only if its detail page
  actually presents Event markup.
- **The site publishes dates, not times.** `startDate` is currently a calendar
  date with no clock time, so events are stored date-only.

### Time precision

`starts_on` / `ends_on` hold calendar dates. `starts_at` / `ends_at` hold exact
timezone-aware instants and are set **only** when the source states one, in
`Europe/Tallinn`. A null `starts_at` means "the source did not say", never
midnight. **A time is never inferred from prose.**

Only upcoming events are published; an event ending today is still upcoming, and
past events are excluded both at import and at read time. Ordering is
chronological, then title, then stable key. Pagination is followed until 20 valid
events are gathered, no next page exists, or `KODA_EVENTS_MAX_PAGES` (5) is
reached. At most `KODA_EVENTS_MAX_ITEMS` (30) are published.

An unreachable detail page skips that one event rather than losing the whole
calendar.

## Normalisation and checksums

The checksum decides whether anything changed, so it must describe **the data
DashKoda consumes**, not the bytes a website sent. A page re-renders with
different whitespace or a new build hash on every deploy; hashing the raw
response would republish identical data every morning.

Each collector therefore normalises its source into a small structure holding
only the consumed fields and hashes that. Canonical JSON is UTF-8, sorted keys,
fixed list order, stable date formatting, compact separators, and **no fetch
timestamp inside** — when the data is identical the checksum is identical. When
it was fetched is recorded separately on the observation.

## Artifacts and publication

Each source registers a **metadata-only** `SourceArtifact`: a fixed non-secret
reference label, the server-computed SHA-256 and byte size of the canonical
JSON, and no stored file. Nothing is written under `SOURCE_ARTIFACT_ROOT`, and
the admin offers no download because there is nothing to download.

Publication is all-or-nothing per source, inside one transaction, retiring the
previous current record. Identical normalised content reports `unchanged` and
creates no duplicate snapshot, artifact or successful live import. A dry run
validates, records a dry-run `ImportRun`, publishes nothing, and never blocks a
later live import of the same checksum.

## Failure isolation

Each source has its own advisory lock, import run and transaction:

- a membership failure does not block news or events;
- a news failure does not block membership or events;
- an events failure does not block membership or news;
- a failed source keeps its previous good data and records the failure.

None of the three locks collides with the legal-work synchronisation.

## Collection rules

Shared transport lives in `apps/core/public_http.py`: HTTPS only, a
caller-supplied host allowlist (`www.koda.ee`, `koda.ee`), a descriptive
DashKoda User-Agent, explicit connect and read timeouts, bounded retries
honouring `Retry-After`, conditional requests with `ETag`/`Last-Modified` where
the source supplies them, a streamed size cap, content-type checks, and
redirects followed by hand so each hop is validated before it is requested.

No cookies, no authentication, no response body in any log, and **no route,
form or setting through which anyone can introduce a URL**. A page render never
contacts Koda.ee.

## Command

```powershell
docker compose exec -T web python manage.py sync_koda_public --source all --json
docker compose exec -T web python manage.py sync_koda_public --source news
docker compose exec -T web python manage.py sync_koda_public --dry-run --json
```

`--source` accepts `all` (default), `membership`, `news` or `events`.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | every requested source imported or was unchanged |
| `1` | every requested source failed |
| `2` | **degraded** — at least one failed while another succeeded |
| `3` | no requested source could take its lock |

A partial failure returns non-zero on purpose, so a scheduled job shows up as
degraded rather than quietly losing one feed, while the sources that did succeed
keep their freshly published data.

`--json` emits exactly one line carrying results, sanitized details and
aggregate counts only — never a member row, a registration code, a feed body,
article HTML or event-page HTML.

## Freshness in the interface

Each section states its own state honestly: never connected, imported with an
observation time, unchanged since the last import, or failed while still showing
the last good data with a restrained warning. Viewers never see exception text.

## 05:40 scheduling

DashKoda contains no scheduler. A template is provided at
[`ops/unraid/sync_koda_public.sh.example`](../ops/unraid/sync_koda_public.sh.example).
Copy it, set `DASHKODA_DEPLOYMENT_DIRECTORY`, make it executable, run it once by
hand, and only then schedule:

```text
5 7 * * *
```

Five minutes after the legal-work job, so the two keep separate logs. They take
different locks and could safely overlap; the offset is for readability.

**Cron uses the host clock.** Confirm the host is on `Europe/Tallinn` — Estonia
observes daylight saving, so a host on UTC runs this an hour off for part of the
year. **This pull request does not install or enable the schedule.**

## Limitations

- The member count reflects the **public directory**, which may lag or differ
  from any internal membership record. It is not an accounting figure.
- The news feed publishes no category, so news cannot be grouped by section.
- The events calendar publishes dates without times, so most events show a date
  only. This is the source's precision, not a DashKoda simplification.
- All three are **HTML/RSS/JSON endpoints on a CMS**. A redesign or a Drupal
  upgrade can change their shape; the collectors fail closed and keep the
  previous data rather than importing something they no longer understand.
- Anonymous public endpoints carry no availability guarantee.

## Future possibility

A dedicated Drupal REST export — a stable, versioned JSON endpoint per dataset,
agreed with whoever maintains koda.ee — would remove the HTML parsing entirely
and give events real start and end times. That is the natural next step if these
feeds prove useful; nothing in this design blocks it, because each collector is
the only thing that knows its source's shape.
