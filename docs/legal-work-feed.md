# Legal-work feed (Õigusloome)

The first DashKoda module backed by real business data. It imports one prepared
Excel workbook from OneDrive, publishes it as an immutable snapshot, and renders
it at `/oigusloome/`.

**DashKoda never reads the lawyers' operational workbook and never writes to
OneDrive.** A separate preparation utility turns that operational file into the
canonical workbook described here; DashKoda only consumes the result, read-only.

## What the dashboard shows, and what it deliberately does not

The page is an analytical surface with one URL. A `fookus` parameter selects
which view is rendered — overview, workflow, active matters, opinions, member
feedback, register — and it is validated against a closed set, so an unknown
value resolves to the overview rather than raising. There is no SPA and no
client-side routing. `apps/legal_work/analytics.py` owns the metric definitions
and `docs/legal-work-intelligence.md` documents them.

Shown: current-year volume, opinions sent, a same-date year-on-year comparison,
active matters by stage, monthly inflow and output, the long-term opinion
series, consultation-window median and mean, active-topic age, deadline
pressure, member-feedback counts, the whole-register search, and the data's
freshness and coverage.

Deliberately absent, in the model as well as the interface: **the responsible
lawyer**, lawyer workload, **member identities**, any AI interpretation, any
forecast, and any composite score. These are not hidden columns — the fields do
not exist, so they cannot leak.

Three further things are absent because the data cannot support them, not
because the interface lacks room:

- **No member response rate.** Schema 1.2 carries `feedback_member_count` and
  `feedback_requested_member_count`, but the first is not a subset of the
  second: members also answer through newsletters and general calls, and the
  register contains matters where more members answered than were asked
  directly. The two counts are shown separately and never divided.
- **No opinion count derived from documents.** The opinion catalogues and the
  public Koda.ee corpus resolve *evidence* for a matter. The authoritative
  "opinion sent" fact stays `sent_status = SENT` together with the date that
  proves it.
- **No merged categories.** `stage`, `recipient` and `act_type` are free text
  that has drifted across the register's history — one ministry was renamed and
  another was reorganised with a changed remit. Exact source values are kept,
  because no automatic rule can tell a spelling variant from a reorganisation.

## The workbook contract

Four sheets: `CONTROL`, `OVERVIEW`, `DATA`, `WARNINGS`.

`DATA` is authoritative and must contain the Excel Table **`tbl_oigusloome`**
with exactly these columns, in this order:

```text
record_id, source_year, source_nr, topic, act_type, received_date,
deadline_date, sent_date, sent_status, recipient, stage, stage_key,
next_step, is_open, warning_codes, source_row, refreshed_at
```

`OVERVIEW` is formatted for people and is **never** read as a data source.
Deriving records from cell positions would break the moment someone adjusted
the layout.

`CONTROL` must provide `dataset_key`, `schema_version`, `source_file_name`,
`source_sheet`, `source_modified_at`, `source_sha256`, `generated_at`,
`reporting_date`, `total_record_count`, `open_record_count`,
`sent_record_count`, `not_sent_record_count`, `warning_record_count`,
`refresh_status` and `generator_version`. Extra keys are ignored rather than
rejected, so the generator can add metadata without breaking the importer.

`dataset_key` must be `oigusloome`. Supported `schema_version` values are
**`1.0`, `1.1` and `1.2`**.

### Why three schema versions

The brief specified `1.0`; the generator emits `1.1`. The `DATA` table is
identical between them — 1.1 only added CONTROL metadata (`overview_year`,
`preview_limit`) and multi-year support. Rather than silently accepting any
version, the importer holds an explicit supported set and rejects everything
else. The version a file declared is recorded on its snapshot.

### Why row uniqueness is a composite

`source_row` is the row number inside its own year sheet, so it repeats across
years — in the real workbook a single `source_row` value appears up to three
times. Uniqueness is therefore `(snapshot, source_year, source_row)`, alongside
`(snapshot, record_id)`. A plain unique constraint on `source_row` would reject
the real workbook.

### Validation rules

The importer rejects the whole file, rather than repairing it, when:

- a sheet is missing, or the `tbl_oigusloome` table is absent;
- column names or their order differ;
- `dataset_key` is wrong or `schema_version` unsupported;
- a `record_id`, or a `(source_year, source_row)` pair, repeats;
- a cell inside `DATA` contains a formula;
- a date column holds text, or `is_open` is not a real boolean;
- `sent_status` is outside `pending / sent / not_sent / invalid`;
- a `sent` record has no `sent_date`, or a non-sent record carries one;
- `topic` is missing;
- `CONTROL` disagrees with the rows actually parsed.

`warning_record_count` counts *records carrying at least one code*, not rows on
the `WARNINGS` sheet: a record with two warnings appears twice there and counts
once here.

## Data model

| Model | Holds |
| --- | --- |
| `LegalWorkSnapshot` | one complete import: source, artifact, import run, declared schema version, reporting date, counts, `is_current` |
| `LegalWorkItem` | one immutable row belonging to a snapshot |
| `LegalWorkFeedState` | when the remote was last checked, last changed, last imported, and how the last attempt ended |

### Snapshot publication

Publication is all-or-nothing:

1. the whole snapshot and all its rows are written inside one transaction;
2. the previous current snapshot is retired and the new one becomes current;
3. the import run is completed and the audit events are recorded.

If anything fails, the transaction rolls back, the import run is closed as
failed, and **the previously current snapshot stays exactly as it was**. A
partial snapshot is never visible.

A database constraint enforces at most one current snapshot per source.
Snapshots are immutable apart from the `is_current` flag, which has to move;
rows are fully immutable. Neither is editable through the admin.

## Manual import

```powershell
docker compose exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx --dry-run
docker compose exec -T web python manage.py import_oigusloome --file /path/to/dashkoda_oigusloome.xlsx
```

It registers the file through the ordinary source service, reuses an existing
artifact when the checksum matches, and then runs **the same importer** the
scheduled sync uses. A dry run validates everything and publishes nothing.

## One recurring collection route

| Route | Command | Needs | Status |
| --- | --- | --- | --- |
| public sharing link | `sync_oigusloome_public` | `OIGUSLOOME_PUBLIC_URL` | the supported recurring route |
| manual file import | `import_oigusloome` | a local workbook path | operator-run, not scheduled |

A Microsoft Graph route also existed. It was **retired** because it never
completed live acceptance, was not required by the production architecture and
had already drifted behind the public route once. Its client, its
`sync_oigusloome` and `resolve_oigusloome_share` commands, its five environment
variables, its ops script and the `msal` dependency are all gone, and
`tests/legal_work/test_graph_retired.py` keeps them gone.

Both surviving entry points publish through the same importer, the same import
registry and the same immutable snapshot. They differ only in how the bytes
arrive and in what is kept afterwards.

The recurring route is not installed as a schedule by this repository. An
administrator has installed it on the pilot host, at 05:30 `Europe/Tallinn`; see
[deployment-status.md](deployment-status.md) for how that host expresses Tallinn
time and what the job's log now records.

## The public read-only sharing link

```text
public read-only OneDrive link
  → scheduled outbound HTTPS download
  → temporary XLSX
  → metadata-only artifact
  → existing importer
  → PostgreSQL snapshot
```

**No Microsoft Entra application, Graph credential, rclone, Power Automate,
webhook or upload endpoint is involved.** One outbound HTTPS request per run.

### Configuration

One variable:

```text
OIGUSLOOME_PUBLIC_URL
```

The complete view-only sharing URL for `dashkoda_oigusloome.xlsx`. It is
optional and blank by default: the web application starts and every page renders
without it, and only `sync_oigusloome_public` requires it. When it is missing
that command exits `1` with a message naming the variable — never its value.

**Treat the URL as a bearer-style secret.** The link is anonymously readable, so
whoever holds it can download the workbook. It belongs only in the server's own
environment file. It is never committed, never written to PostgreSQL, never
logged, never placed in an audit summary and never shown in the interface or the
admin. The command deliberately offers no `--url` option, so the URL cannot
reach shell history or a process listing either.

### What one run does

1. takes the feed's PostgreSQL advisory lock, so overlapping runs cannot both
   import;
2. ensures the `oigusloome-onedrive` data source exists and records
   `last_checked_at`;
3. adds or replaces `download=1` on the configured URL — without it Microsoft
   answers with an HTML viewer page rather than the file;
4. downloads to a secure temporary directory, following at most five redirects,
   every hop HTTPS, with explicit connect and read timeouts and no
   authentication header or persisted cookie;
5. proves the result is an XLSX package: non-zero size, the ZIP signature,
   `zipfile.is_zipfile`, and the `[Content_Types].xml` and `xl/workbook.xml`
   members. `Content-Type` is a signal only — Microsoft may legitimately label a
   real download `application/octet-stream`, and an HTML page labelled
   `application/octet-stream` is still refused;
6. computes SHA-256 and the byte count from the bytes actually received;
7. reuses the artifact with that checksum, or registers a new **metadata-only**
   one;
8. runs the existing importer against the temporary path;
9. publishes the snapshot and updates the feed state;
10. deletes the temporary directory — on success, on unchanged, on validation
    failure, on download failure and on any unexpected exception.

**No permanent copy of the workbook is kept.** Nothing is written to
`SOURCE_ARTIFACT_ROOT` by this route.

### The metadata-only artifact

The artifact records what the content *was*, not where to get it again:

| Field | Value |
| --- | --- |
| `source` | `oigusloome-onedrive` |
| `external_reference` | `onedrive-public:oigusloome` |
| `original_name` | `dashkoda_oigusloome.xlsx` |
| `mime_type` | the XLSX MIME type |
| `size_bytes`, `sha256` | computed server-side from the downloaded bytes |
| `file` | **empty** |

`onedrive-public:oigusloome` is a fixed, non-secret label. The sharing URL must
never be the external reference, and the model refuses any reference containing
`@` or `?`, so a URL with query parameters cannot be stored there even by
mistake.

An artifact is importable when it carries a trusted SHA-256, not when it still
has a file. An external reference registered *without* a checksum remains
non-importable, exactly as before.

### Idempotency: the checksum is authoritative

There is no etag and no remote modification time to compare, so every run
downloads and the digest decides:

| Situation | Result |
| --- | --- |
| same checksum, a successful live import exists | `unchanged` |
| same checksum, only dry-run imports | reuse the artifact, run the live import |
| same checksum, only failed imports | reuse the artifact, retry the import |
| new checksum | register a new metadata-only artifact and import |

The third and fourth rows are why `--force` is unnecessary and why a dry run can
never block the later live import of the same bytes.

### Commands

```powershell
docker compose exec -T web python manage.py sync_oigusloome_public --dry-run --json
docker compose exec -T web python manage.py sync_oigusloome_public --json
```

Exit codes: `0` imported, unchanged or a successful dry run; `1` failed; `3`
another synchronisation was already running.

`--json` emits exactly one line containing only `result`, `detail`,
`snapshot_id`, `reporting_date`, `rows_imported`, `dry_run` and warning-code
counts. No URL, host, path, header, cookie, workbook content or topic name.

A dry run validates the live workbook and publishes nothing; only
`last_checked_at` moves.

On failure the previous snapshot stays current, the feed state records `failed`
with a sanitized truncated message, and the dashboard keeps showing the last
good data with an explicit "last check failed" note.

### Feed state in this mode

`last_checked_at`, `last_successful_sync_at`, `last_changed_at`, `last_result`,
`last_error_summary`, `remote_size_bytes` and `current_snapshot` are all
maintained. `remote_etag` stays blank and `remote_modified_at` stays null:
this route has no trustworthy non-secret value for either, and the checksum
belongs on the artifact. Storing a digest in an etag field would make both
fields lie.

The three timestamps answer three different questions, and an **unchanged run
is a successful run**:

| Field | Meaning | Imported | Unchanged | Failed |
| --- | --- | --- | --- | --- |
| `last_checked_at` | the latest attempted check, however it ended | moves | moves | moves |
| `last_successful_sync_at` | the latest check that succeeded | moves | **moves** | unchanged |
| `last_changed_at` | the latest time different content was published | moves | unchanged | unchanged |

That distinction matters operationally: the workbook is regenerated every
morning but usually carries identical bytes, so most days end in `unchanged`.
Treating that as "no successful sync" would make a healthy feed read as
untouched for weeks. A failure moves only `last_checked_at`, so the previous
success is never overwritten by a bad morning, and the dashboard keeps showing
the last good data with an honest "last check failed" note.

A dry run moves `last_checked_at` and nothing else.

### Limitations of the public-link route

- **Anonymous-link access can be revoked or forwarded.** Whoever holds the URL
  can download the workbook, and an administrator who regenerates or revokes the
  link breaks the synchronisation until `OIGUSLOOME_PUBLIC_URL` is updated. A
  revoked link surfaces as a `403`/`404` failure, not as silent staleness.
- **Microsoft may change sharing-link behaviour.** The `download=1` parameter and
  the response shape are Microsoft's, not a documented API contract. If the
  response becomes HTML the download is refused rather than imported, and the
  previous snapshot stays published.
- There is no change notification. The workbook is fetched once each morning, so
  a mid-day regeneration is picked up the next day.
- Because no file is retained, the staff artifact download is unavailable for
  these artifacts by construction; the checksum is the record of what was
  imported.

## The retired Microsoft Graph route

DashKoda used to carry a second, app-only Microsoft Graph collection route:
`sync_oigusloome`, a one-off `resolve_oigusloome_share` identifier resolver, an
Entra application with `Files.Read.All`, five environment variables and the
`msal` dependency.

It is **retired and removed**, because:

- it never completed live acceptance — no Graph credential ever existed for
  this project, so the path was never exercised against the real API;
- the production architecture does not need it: the public sharing link is the
  accepted route and has completed live acceptance;
- it had already drifted behind the public route once. The dry-run
  artifact-reuse defect fixed in PR #18 existed only in the Graph flow, because
  a correction made to the public route was never carried across;
- it was the most security-sensitive and least-exercised code in the
  repository: a tenant-wide read credential, app-only token acquisition and a
  signed-URL download, all unverified.

Nothing about the retirement changes what the dashboard shows or how the
surviving route publishes. If a Graph route is ever wanted again it should be
designed against the current shared downloader rather than restored from
history, and it must complete live acceptance before being documented as
supported.

## Failure behaviour

On any failure the previous snapshot stays current, the feed state records
`failed` with a sanitized and truncated message, no temporary file remains, and
the import run is closed as failed. The dashboard keeps showing the last good
data together with an explicit "last check failed" note.

## A collapsed workbook is refused

This feed is where the failure actually happened. A convention change in the
source `NR` column made every 2025 and 2026 record fail to produce a canonical
id, and the export that reached the dashboard held only the 2024 rows. Nothing
was broken in a way anything could see: the workbook was valid, internally
consistent and much smaller, and it was accepted.

The import now compares itself with what is already published. When the incoming
record count falls below `FEED_COLLAPSE_MIN_RATIO` (default `0.5`) of the
snapshot currently on the dashboard, it **fails and publishes nothing**, leaving
the previous snapshot in place. Growth is never blocked, a first import is never
blocked, and an ordinary shrink above the floor publishes normally.

When the dataset has genuinely shrunk, answer the question once:

```bash
python manage.py sync_oigusloome_public --allow-collapse
```

`import_oigusloome` takes the same flag. A dry run performs the same check, so it
reports the refusal instead of passing and failing later.

## Data freshness in the interface

Four distinct states, because "the page loaded today" is not a claim about the
data:

| State | Shown |
| --- | --- |
| never synchronised | `Andmeallikas ei ole veel ühendatud.` |
| imported | `Andmed seisuga <workbook reporting date>` and the last successful sync time |
| unchanged | last checked time, plus `Andmed ei ole pärast eelmist importi muutunud.` |
| failed with older data | last successful sync, and `Kuvatakse viimase eduka impordi andmeid.` |

Viewers never see exception text. A sanitized diagnostic is visible to staff
through the feed-state and import-run admin.

## 07:00 scheduling

DashKoda contains no scheduler: no Celery, no Redis, no APScheduler and no
polling thread. Scheduling belongs to the host.

One template is provided, for the one recurring route:

- [`ops/unraid/sync_oigusloome_public.sh.example`](../ops/unraid/sync_oigusloome_public.sh.example)
  — the public sharing link

Copy it, replace `<DASHKODA_DEPLOYMENT_DIRECTORY>`, make it executable, run it
by hand once, and only then schedule:

```text
0 7 * * *
```

Both templates take the same host `flock` path, so scheduling both by mistake
cannot start two runs at once. The PostgreSQL advisory lock is the primary
overlap protection and holds even across hosts and containers; the `flock` is
defence in depth.

**Cron uses the host clock.** Confirm the Unraid host is on `Europe/Tallinn`
before enabling it — Estonia observes daylight saving, so a host left on UTC
runs the job an hour off for part of the year:

```bash
timedatectl
date +'%Z %z %H:%M'
```

Expect `EET +0200` in winter and `EEST +0300` in summer. The application's own
advisory lock is the overlap guarantee; the `flock` in the template is optional
defence in depth.

**This pull request does not install or enable the schedule.**

## Secret handling

No token, client secret, sharing URL, redirect URL, signed URL, temporary path
or workbook content is ever written to the database, the audit trail, the logs or
the interface. `LegalWorkFeedState` stores only non-secret content metadata
(etag, size, modification time). Import diagnostics carry warning-code counts,
never rows. Audit summaries carry the source slug, the checksum, the byte size,
record counts, the reporting date and the result — never file content, never a
topic name and never a URL.

## Current limitations

- **Live acceptance of the full public-link import has not been completed.** The
  download, URL handling, XLSX validation and temporary-file lifecycle *have*
  been verified against the live link, repeatedly and against more than one
  published revision of the workbook. The remaining steps — publishing a
  snapshot, reporting unchanged on a repeat run — need a PostgreSQL instance and
  a workbook published with its Excel Table intact; see the publishing rule
  above.
- The importer requires the `tbl_oigusloome` Excel Table. A workbook generated
  without it is rejected.
- Only the current snapshot is read. There is no history view and no trend.
- Byte-identical content cannot be re-published: the import registry allows one
  successful live import per import key. The public route needs no `--force`
  and offers none.
- No chart is rendered: a list communicates these values more honestly.

### Publishing the workbook without breaking it

The workbook must reach OneDrive **exactly as the generator wrote it**. Uploading
it by opening it in Excel Online and saving silently strips the `tbl_oigusloome`
Excel Table from `DATA`, and the importer then rejects the file.

The symptom is unmistakable: the file *grows* by a few kilobytes, `WARNINGS`
keeps `tbl_oigusloome_warnings`, only one table part remains inside the package,
and the error is `DATA lehel puudub Exceli tabel 'tbl_oigusloome'`. Every count
can still be perfectly correct.

Upload it as a plain file replace — *Upload → Files* in the OneDrive web
interface, or let the sync client push it — and never through the "Open in Excel
Online" round trip. The same applies to any manual edit: opening the generated
workbook in Excel to look at it, then saving, is enough to break it.

If it does get stripped, the round-tripped copy also syncs back down and
overwrites the good local file. Re-run the generator, or recover the earlier
version from OneDrive version history.

This is a publishing-workflow rule, not something the importer should tolerate.
The Excel Table is what makes `DATA` an addressable, contractually-shaped table
rather than an arbitrary grid, so the contract must not be relaxed to accept a
workbook without it.

## Rollback to a previous snapshot

Snapshots are retained, so recovery is a matter of moving the current flag.
There is no admin action for it by design; use the shell deliberately:

```powershell
docker compose exec -T web python manage.py shell
```

```python
from apps.legal_work.models import LegalWorkSnapshot
current = LegalWorkSnapshot.objects.get(is_current=True)
target = LegalWorkSnapshot.objects.get(pk=<previous id>)
current.is_current = False
current.save(update_fields=["is_current"])
target.is_current = True
target.save(update_fields=["is_current"])
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `puudub Exceli tabel 'tbl_oigusloome'` | the generator did not write the DATA table |
| `Toetamata skeemi versioon` | the workbook declares a version outside `{1.0, 1.1, 1.2}` |
| `CONTROL ei ole DATA lehega kooskõlas` | the workbook's own summary disagrees with its rows |
| `CONTROL lehe väljad on tühjad` | a required CONTROL key was written with an empty value |
| `DATA lehel puudub Exceli tabel` after an upload | the workbook was round-tripped through Excel Online, which strips the table |
| `Avaliku töövihiku seadistus on puudulik` | `OIGUSLOOME_PUBLIC_URL` is unset |
| `Vastuseks tuli text/html asemel Exceli faili` | the link no longer permits downloading, or it now needs sign-in |
| `Jagamislink ei ole kättesaadav (404)` | the sharing link was revoked or regenerated |
| `Jagamislink keeldus ligipääsust (403)` | anonymous access to the link was withdrawn |
| `Allalaaditud ZIP-pakend ei ole XLSX töövihik` | the response was a valid archive but not a workbook |
| exit code `3` | another synchronization was still running |
| `importrun_unique_successful_live_import` | this exact content already imported successfully |
