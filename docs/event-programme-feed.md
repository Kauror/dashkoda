# Event-programme feed (Sündmuste programm)

**The Excel export is the authoritative event programme.** It is the source of
truth for the `/sundmused/` page, for the overview's event figures, for the shell
freshness row's event domain, and for all historical event reporting. It imports
one prepared workbook from OneDrive, publishes it as an immutable snapshot, and
reads only PostgreSQL when rendering.

**One current snapshot carries the whole available history.** The generator
exports the complete programme every morning, so a year, a month, a tag or a
past-event total is read from that one snapshot. Rows from several snapshots are
never combined to reconstruct a past: two exports of different vintages must not
contribute to the same total.

**DashKoda never reads the operational service-code workbook and never writes to
OneDrive.** An Office Script and a scheduled Power Automate flow turn that
operational file into the canonical export described here; DashKoda only
consumes the result, read-only.

## Not to be confused with `apps.events`

Two different things are called "sündmused", and they must not be merged:

| | `apps.events` | `apps.event_programme` |
| --- | --- | --- |
| Source | the public koda.ee listing pages | the operational service-code workbook |
| Answers | what did we announce publicly | what did the Chamber actually run |
| Collected by | `sync_koda_public` | `sync_event_programme` |
| Role | supplementary | **authoritative** |

They count different things over different periods. Never extend one series with
the other, and never present two unlabelled event totals side by side.

`apps.events` is a separate public-calendar feed that keeps its own immutable
snapshots and its own schedule. **The public feed never overrides an Excel
field**, and it may not produce a historical count, a current-year total, a tag,
month or type total, a past-event total, an event identity or a link. It has no
route of its own; `/sundmused/` is the programme's page and names the public
calendar there as a secondary connection with its own state and its own count of
publicly announced upcoming events.

## What the page reads, and what it never does

Page rendering reads PostgreSQL and nothing else. The workbook is downloaded only
by `sync_event_programme`; no page request contacts OneDrive, koda.ee or any
other remote system, and a failed refresh preserves the last good snapshot with an
honest "last check failed" note.

### Public links come from the workbook only

`EventProgrammeItem.public_url` is the event's public page. It is populated by the
workbook generator and by the hand-maintained `DASH_URL_OVERRIDES` sheet in the
operational workbook — **the only two places a linking decision is made.**

**No fuzzy matching occurs.** A record is never linked on the basis of a similar
title, a normalised title, an exact title alone, a title plus a date, a
service-code inference, event order, a search-engine result or a redirect
discovered while rendering. When `public_url` is blank the event name renders as
plain text: no dead anchor, no fabricated "not found" address, and the event is
not hidden.

An exact canonical-URL comparison against the public-feed snapshot may be used
only to observe that the same URL also exists there. It never provides or
replaces a URL, and the first implementation does not perform it.

There is deliberately **no URL editor in DashKoda**, in the admin or anywhere
else. A linking correction belongs in `DASH_URL_OVERRIDES`.

### Unknown dates remain records

A row whose operational date text nobody could parse is still a real event the
Chamber ran. It keeps `event_status = date_unknown`, an empty year, month and
quarter, and it is neither dropped nor given an invented date. The page shows
"Kuupäev teadmata" for it, and discloses how many such records exist with a link
that opens them across every year — because the default period filter, which
filters by year, cannot show a record that has no year.

### Filters use event dates, not source-sheet years

`source_year`, `source_sheet` and `source_row` describe where the row sat in the
operational workbook. They are provenance, not a date. Every period figure and
every period filter uses `event_year`, `event_month_key`, `event_quarter` and
`start_date`; `source_year` is never presented as when an event happened.

### Prices and participant counts

**Pricing is not imported.** The workbook carries member, non-member and later
prices, their parsed euro values, a discount code and a price status; the importer
validates the columns so a generator change cannot pass unnoticed and then
discards them. No model field, no migration and no interface element exists for
any of them.

**Participant and registration counts are not available.** No accepted
authoritative source provides them, and they are never inferred from "kohad
täis", availability wording, a public registration page, an event status or
capacity text. Both are deferred product decisions, not omissions to be worked
around.

## Where the workbook comes from

```text
Teenuste koodid syndmustele 2024.xlsx   operational, on team SharePoint
  ↓  Office Script "DashKoda – Refresh events export"
  ↓  writes the DASH_* sheets back into that same workbook
  ↓  Power Automate "DashKoda - Publish events export", daily 06:30 Tallinn
dashkoda_events.xlsx                    the canonical export DashKoda reads
```

The flow trims a staging copy to the six `DASH_*` sheets and validates it before
replacing the canonical file, and it fails without touching that file when
validation does not pass. DashKoda therefore never sees a partially refreshed
export — but it re-validates everything anyway, because a feed that trusts its
producer has no way to notice when the producer changes.

## The workbook contract

Six sheets: `DASH_CONTROL`, `DASH_EVENTS`, `DASH_EVENT_OCCURRENCES`,
`DASH_REVIEW`, `DASH_TAG_MAP`, `DASH_URL_OVERRIDES`.

`DASH_EVENTS` is authoritative and must contain the Excel Table
**`tbl_dash_events`** with all 46 columns, in the generator's exact order. The
other five sheets must be **present with their tables** but are never read as a
data source: occurrences and review findings are the generator's working
material, and the tag map and URL overrides are hand-maintained inputs whose
authoritative copy lives in the operational workbook. Requiring them proves the
file is a complete export rather than a truncated one; storing them would put
someone else's editing surface into DashKoda.

`DASH_CONTROL` must provide `dataset_key`, `schema_version`, `generator_name`,
`generator_version`, `source_workbook_name`, `refresh_status`,
`canonical_event_count`, `qualifying_occurrence_count`, `excluded_event_count`,
`repeated_service_code_count`, `linked_public_url_count`,
`distinct_short_name_count`, `blocking_error_count`, `warning_count`,
`export_refreshed_at` and `last_successful_refresh_at`. Extra keys are ignored
rather than rejected, so the generator can add metadata without breaking the
importer.

`dataset_key` must be `events`. The only supported `schema_version` is **`1.0`**.

### Two properties of this generator that the parser accommodates

Both are real characteristics of the Office Script, not defects, and both differ
from the legal-work export:

- **Every `DASH_*` table starts below a two-row banner** written for the people
  who maintain the operational workbook. The header row is located from the
  table's declared range rather than assumed, so changing the banner cannot
  silently shift the parser onto the wrong row.
- **`DASH_CONTROL` is a text key/value table**, so every count arrives as a
  string. The importer accepts a plain decimal string and nothing else: `"12.0"`,
  `"1 186"`, a boolean and an empty cell are all rejected.

### What DashKoda stores, and what it deliberately does not

Stored: identity and service code, name, dates and the derived year, month and
quarter, status, tag and event type, delivery mode, include status, public URL
and link status, the source provenance, and the quality flags.

Deliberately absent, in the model as well as the interface: **all pricing**
(member, non-member, later, and their parsed euro values), discount codes,
`price_status`, every `*_raw` echo column, `added_date` and
`planning_lead_days`. These are verified in the header — so a generator change
cannot pass unnoticed — and then discarded. They are not hidden columns; the
fields do not exist, so they cannot leak.

### Validation rules

The importer rejects the whole file, rather than repairing it, when:

- a sheet is missing, or a sheet's Excel Table is absent;
- column names or their order differ;
- `dataset_key` is wrong or `schema_version` unsupported;
- `refresh_status` is anything but `ready` or `ready_with_warnings`;
- `blocking_error_count` is not zero — the generator's own gate;
- an `event_id` or a `service_code` repeats;
- `event_name` is missing;
- a date column holds text, or `review_required` is not a real boolean;
- `end_date` precedes `start_date`, or exists without one;
- `DASH_CONTROL` disagrees with the rows actually parsed, on either the event
  count or the linked-URL count.

`start_date` is **nullable**, and that is not a defect either. In the real export
33 events carry date text nobody could parse. Null means "the source did not
say", never "no date": the event still happened, still counts, and still
appears — with `event_status` `date_unknown` and an empty year, month and
quarter rather than an invented one.

## A collapsed workbook is refused

A workbook can be perfectly valid and still be wrong. If the generator stops
matching most of its source rows, what arrives is a well-formed export holding a
fraction of the events — no error, no warning, just a much emptier programme. The
contract checks above all pass, because the file genuinely is consistent with
itself.

So the import also compares itself with what is already published. When the
incoming event count falls below `FEED_COLLAPSE_MIN_RATIO` (default `0.5`) of the
snapshot currently on the dashboard, the import **fails and publishes nothing**;
the previous snapshot stays exactly where it was and the failure is disclosed in
the usual way. The refusal names both counts.

The comparison is one-directional and deliberately narrow:

- growth is never blocked, however large;
- a first import is never blocked, because there is nothing to compare with;
- an ordinary shrink above the floor publishes normally.

It is a question, not a ceiling. When the programme has genuinely shrunk, answer
it once:

```bash
python manage.py sync_event_programme --allow-collapse
```

A dry run performs the same check, so `--dry-run` tells you whether the real
import would be accepted rather than passing quietly and failing later.

## Data model

| Model | Holds |
| --- | --- |
| `EventProgrammeSnapshot` | one complete import: source, artifact, import run, declared schema version, export timestamp, counts, `is_current` |
| `EventProgrammeItem` | one immutable event belonging to a snapshot |
| `EventProgrammeFeedState` | when the remote was last checked, last changed, last imported, and how the last attempt ended |

Everything on a snapshot except `is_current` is fixed once written; an item
cannot be changed at all. Publishing a new snapshot retires the previous one
inside one transaction, and a partial unique constraint makes "two current
snapshots" unrepresentable.

`tag_key` and `event_type_key` carry no choices on purpose. They come from the
hand-maintained `DASH_TAG_MAP`, so the vocabulary grows whenever someone
classifies a new short name — and a newly classified name must import rather
than fail validation.

## How the dashboard reads it

`apps/event_programme/selectors.py` is the only read path. It reads PostgreSQL and
the **current** snapshot, and it never consults `apps.events`.

| Selector | Answers |
| --- | --- |
| `get_current_event_programme_snapshot()` | which snapshot is published |
| `get_event_programme_summary()` | freshness, size and the export as-of date |
| `get_event_programme_filter_options()` | every year, month, quarter, tag, type, delivery mode and status the snapshot actually contains |
| `get_filtered_event_programme_items(...)` | the table's rows, filtered and deterministically ordered |
| `count_events_starting_within(...)` | events starting in the next 30 days, today included |
| `count_events_started_within(...)` | events started in the previous 30 days, today excluded |
| `count_events_for_year(...)` | events whose own start date falls in a year |
| `count_unknown_date_events()` | records whose date could not be read |
| `count_linked_events()` | records the workbook linked to a public page |
| `count_review_required_events()` | records the generator flagged for review |

`EventProgrammeSummary` is an immutable dataclass using the shared
`FeedSummaryMixin`, so "connected", "stale after a failed check" and the state
badge mean exactly what they mean for every other feed. Its `observed_at` is the
workbook's own `export_refreshed_at` — the moment the Chamber's generator produced
the export, which is what the figures describe.

### Filter contract

Server-side GET parameters on `/sundmused/`, all validated against the current
snapshot before they reach a query:

| Parameter | Values | Filters on |
| --- | --- | --- |
| `q` | free text | event name or service code, case-insensitive |
| `year` | a year in the snapshot, or `all` | `event_year` |
| `month` | `01`–`12` | the month tail of `event_month_key` |
| `quarter` | `Q1`–`Q4` | `event_quarter` |
| `tag` | a `tag_key` | `tag_key`; `tag_label` is the visible label |
| `event_type` | an `event_type_key` | `event_type_key`; `event_type_label` is the label |
| `delivery_mode` | `onsite`, `online`, `hybrid` | `delivery_mode` |
| `status` | `past`, `ongoing`, `upcoming`, `date_unknown` | `event_status` |
| `public_link` | `all`, `linked`, `unlinked` | whether `public_url` is set |
| `review` | `all`, `required`, `clear` | `review_required` |
| `page` | a page number | server-side pagination, 50 rows |

Nothing is hard-coded: a year, tag, type, quarter, month or delivery mode is
offered because the snapshot contains it, so a vocabulary the Chamber grows in
`DASH_TAG_MAP` appears without a code change. A value the snapshot does not
contain falls back to "not filtered" rather than producing an unexplained empty
table.

The default period is the **current calendar year** when the snapshot has it, and
the latest known event year otherwise. "Kõik aastad" is an explicit choice, the
active period is displayed, and clearing every filter is one link.

Ordering is: known dates newest first, undated records after every dated one, then
a stable tie-break on event name and service code — so two events on one day never
swap places between requests or between pages. Pagination links, the clear-filters
action and the data-quality links are rebuilt from the validated filter state, so
a link carries exactly the filters that were applied and a bogus parameter cannot
travel from page to page.

### Read-only admin

`EventProgrammeSnapshot`, `EventProgrammeItem` and `EventProgrammeFeedState` are
registered through the project's shared `ReadOnlyAdmin` for inspection only.
Imported rows are immutable by construction, there is no browser-based editing of
an imported event, and there is no URL editor — see above.

## Collection

One route: a **view-only public sharing link**, read from
`EVENT_PROGRAMME_PUBLIC_URL`. No Microsoft Entra application, no Graph
credentials, no inbound endpoint.

```bash
python manage.py sync_event_programme --json
```

- There is **no `--url` option**. The URL is a bearer-style secret; it comes
  only from the environment, so it never enters shell history or a process
  listing. It appears in no outcome, feed state, audit summary or log line.
- There is no `--force` and none is needed: the workbook is downloaded every run
  and the content checksum is authoritative. Unchanged bytes report `unchanged`
  and publish nothing.
- The download lives in a temporary directory removed on every exit path, so the
  `SourceArtifact` is metadata-only: a fixed provenance label plus the checksum,
  size and MIME type computed from the bytes that actually arrived.
- Overlapping runs are refused by a PostgreSQL advisory lock.

Exit codes: `0` imported, unchanged or a successful dry run; `1` failed; `3`
another run was already going.

Schedule it after 06:30 Tallinn — `ops/unraid/sync_event_programme.sh.example`
suggests 07:00 and explains the host-timezone trap.

A failure is never destructive. Whatever goes wrong, the previously published
snapshot stays current and the dashboard keeps showing the last good data with
an honest "last check failed" note.

### What the feed-state timestamps mean

An **unchanged run is a successful run**. The export is regenerated every
morning but usually carries identical bytes, so `unchanged` is the normal
healthy outcome rather than a non-event:

| Field | Meaning | Imported | Unchanged | Failed |
| --- | --- | --- | --- | --- |
| `last_checked_at` | the latest attempted check, however it ended | moves | moves | moves |
| `last_successful_sync_at` | the latest check that succeeded | moves | **moves** | unchanged |
| `last_changed_at` | the latest time different content was published | moves | unchanged | unchanged |

A failure moves only `last_checked_at`, so one bad morning never overwrites the
record of the last good one. A dry run moves `last_checked_at` and nothing else.

> `apps/event_programme/public_download.py` and
> `apps/legal_work/public_download.py` are thin wrappers over one shared
> hardened implementation in `apps/sources/public_download.py`. Each wrapper
> names only its own settings key, size cap, user agent and log prefix; the
> transport and validation logic exists once, so a security fix there reaches
> both feeds. `tests/sources/test_public_download.py` runs every behaviour
> against both wrappers and asserts the delegation itself.

## Production acceptance

Run **after** the pull request has been merged and the production phase has been
explicitly authorised. Nothing below is part of the repository change, and no step
here is performed by CI.

### What the server must point at

The accepted source is the canonical published export, `dashkoda_events.xlsx` —
**not** the operational workbook that carries the `KOOD YYYY` sheets and the
editable `DASH_*` sheets. Pointing DashKoda at the operational file would make it
read someone's editing surface.

The sharing URL lives in `EVENT_PROGRAMME_PUBLIC_URL` in the server environment
and nowhere else. It is a bearer-style secret: it is never committed, never passed
through a command-line option (there is none), never printed in a report, and it
reaches no log, audit summary, command output, interface or PostgreSQL row.

### Sequence

1. Deploy merged `main`.
2. Apply migrations.
3. `python manage.py sync_event_programme --dry-run --json`.
4. Verify the contract validated: `result` is `imported` with `dry_run: true`, and
   no snapshot was published.
5. `python manage.py sync_event_programme --json`.
6. Verify exactly one current snapshot exists for the source.
7. Run `python manage.py sync_event_programme --json` again.
8. Verify the result is `unchanged` and nothing new was published.
9. Verify the aggregate counts against `DASH_CONTROL` in the export: canonical
   event count, dated count, linked-URL count, review count.
10. Verify years, tags, unknown dates and public-link counts through PostgreSQL.
11. Open `/sundmused/` and exercise representative filters: a historical year,
    "Kõik aastad", a month, a tag, a combination, the unknown-date link, and page
    two of a filtered result.
12. Only once every check passes, enable **one** daily server schedule.

### Schedule

`05:30 Europe/Tallinn`, using `ops/unraid/sync_event_programme.sh.example`. The
workbook publication flow runs at 06:30, so the import must run after it. One
schedule only: a second would contend for the advisory lock for no benefit.

Do not modify Power Automate, the Office Script, SharePoint or OneDrive as part of
acceptance. The workbook side is already in place; this is DashKoda reading it.
