# Event-programme feed (Sündmuste programm)

The Chamber's own record of the events it ran. It imports one prepared Excel
workbook from OneDrive, publishes it as an immutable snapshot, and reads only
PostgreSQL when rendering.

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

They count different things over different periods. Never extend one series with
the other, and never present two unlabelled event totals side by side.

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
